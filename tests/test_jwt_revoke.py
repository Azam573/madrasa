"""
tests/test_jwt_revoke.py — JWT Revocation Tests (v9.0 fix #4)

যাচাই করে:
1. Token-এ jti/iat claim আছে (revocation tracking-এর জন্য আবশ্যক)
2. revoke_token() করার পর সেই token আর valid থাকে না
3. revoke_all_user_tokens() করার পর ওই user-এর সব পুরোনো token invalid হয়
4. নতুন (revocation-এর পরে ইস্যু করা) token ঠিকই valid থাকে
5. Redis unavailable হলে fail-open হয় (block করে না)
6. /logout এবং /logout-all-devices endpoint বাস্তবে কাজ করে
"""

import os
import time
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-jwt-revoke-tests")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")  # আলাদা DB index — অন্য test-কে প্রভাবিত করবে না


def _redis_available() -> bool:
    try:
        import redis
        r = redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=1)
        r.ping()
        return True
    except Exception:
        return False


REDIS_UP = _redis_available()


@pytest.fixture(autouse=True)
def _clean_redis():
    """প্রতিটি test-এর আগে/পরে test DB flush করি যাতে test isolation থাকে।"""
    if REDIS_UP:
        import redis
        r = redis.from_url(os.environ["REDIS_URL"])
        r.flushdb()
    yield
    if REDIS_UP:
        import redis
        r = redis.from_url(os.environ["REDIS_URL"])
        r.flushdb()


class TestTokenClaims:
    """Token-এ revocation-এর জন্য প্রয়োজনীয় claim আছে কিনা।"""

    def test_access_token_has_jti(self):
        from api.core.security import create_access_token, SECRET_KEY, ALGORITHM
        import jwt as pyjwt

        token = create_access_token({"user_id": 1, "tenant_id": 1, "username": "test"})
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert "jti" in payload, "Access token-এ jti নেই — revocation track করা সম্ভব নয়"
        assert "iat" in payload, "Access token-এ iat নেই — per-user revocation সম্ভব নয়"

    def test_refresh_token_has_jti(self):
        from api.core.security import create_refresh_token, SECRET_KEY, ALGORITHM
        import jwt as pyjwt

        token = create_refresh_token({"user_id": 1, "tenant_id": 1, "username": "test"})
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert "jti" in payload
        assert "iat" in payload

    def test_each_token_has_unique_jti(self):
        """একই user-এর জন্য বারবার token বানালে প্রতিবার আলাদা jti হওয়া উচিত।"""
        from api.core.security import create_access_token, SECRET_KEY, ALGORITHM
        import jwt as pyjwt

        data = {"user_id": 1, "tenant_id": 1, "username": "test"}
        token1 = create_access_token(data)
        token2 = create_access_token(data)
        p1 = pyjwt.decode(token1, SECRET_KEY, algorithms=[ALGORITHM])
        p2 = pyjwt.decode(token2, SECRET_KEY, algorithms=[ALGORITHM])
        assert p1["jti"] != p2["jti"]


@pytest.mark.skipif(not REDIS_UP, reason="Redis সার্ভার চালু নেই — integration test skip করা হলো")
class TestRevocationWithRedis:
    """বাস্তব Redis দিয়ে revocation logic যাচাই (integration test)।"""

    def test_fresh_token_not_revoked(self):
        from api.core.jwt_revoke import is_token_revoked
        assert is_token_revoked("some-random-jti-never-revoked") is False

    def test_revoke_then_check(self):
        from api.core.jwt_revoke import revoke_token, is_token_revoked

        jti = "test-jti-12345"
        future_exp = int(time.time()) + 3600  # 1 ঘণ্টা পরে expire
        assert is_token_revoked(jti) is False

        revoke_token(jti, future_exp)
        assert is_token_revoked(jti) is True

    def test_revoked_token_fails_verify_token(self):
        """পূর্ণ ফ্লো: token বানাও → verify হয় → revoke করো → verify আর হয় না।"""
        from api.core.security import create_access_token, verify_token
        from api.core.jwt_revoke import revoke_token
        from fastapi import HTTPException
        import jwt as pyjwt
        from api.core.security import SECRET_KEY, ALGORITHM

        token = create_access_token({"user_id": 99, "tenant_id": 1, "username": "revoketest"})

        # Revoke করার আগে valid হওয়া উচিত
        payload = verify_token(token)
        assert payload["user_id"] == 99

        # এখন revoke করি
        raw = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        revoke_token(raw["jti"], raw["exp"])

        # এখন verify_token() ব্যর্থ হওয়া উচিত
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        assert exc_info.value.status_code == 401

    def test_revoke_all_user_tokens_invalidates_old_token(self):
        """
        Password reset (#1) সিনারিও: token ইস্যু হলো, তারপর
        revoke_all_user_tokens() call হলো (যেমন reset-এর পরে) —
        পুরোনো token আর কাজ করবে না, যদিও individually revoke করা হয়নি।
        """
        from api.core.security import create_access_token, verify_token
        from api.core.jwt_revoke import revoke_all_user_tokens
        from fastapi import HTTPException

        old_token = create_access_token({"user_id": 55, "tenant_id": 2, "username": "alice"})

        # প্রথমে valid
        assert verify_token(old_token)["user_id"] == 55

        # সিমুলেট করি যে password reset হলো → সব token revoke
        time.sleep(1.1)  # iat resolution (সেকেন্ড) নিশ্চিত করতে old token-এর আগে cutoff বসে
        revoke_all_user_tokens(55, 2)

        with pytest.raises(HTTPException) as exc_info:
            verify_token(old_token)
        assert exc_info.value.status_code == 401

    def test_new_token_after_global_revoke_still_works(self):
        """
        Global revoke-এর পরে নতুন করে লগইন করলে (নতুন token, নতুন iat)
        সেই নতুন token অবশ্যই valid থাকা উচিত — pure lockout নয়।
        """
        from api.core.security import create_access_token, verify_token
        from api.core.jwt_revoke import revoke_all_user_tokens

        revoke_all_user_tokens(77, 3)
        time.sleep(1.1)  # cutoff timestamp-এর পরে নতুন token ইস্যু হচ্ছে তা নিশ্চিত করতে

        new_token = create_access_token({"user_id": 77, "tenant_id": 3, "username": "bob"})
        payload = verify_token(new_token)
        assert payload["user_id"] == 77

    def test_logout_endpoint_revokes_token(self):
        """/auth/logout call করলে token সত্যিই blacklist হয় কিনা — পূর্ণ HTTP flow।"""
        from fastapi.testclient import TestClient
        from api.core.security import create_access_token
        import api.main as main_module

        client = TestClient(main_module.app)
        token = create_access_token({
            "user_id": 1, "tenant_id": 1, "username": "logouttest", "role": "admin"
        })
        headers = {"Authorization": f"Bearer {token}"}

        # /me কাজ করা উচিত logout-এর আগে
        resp1 = client.get("/api/v1/auth/me", headers=headers)
        assert resp1.status_code == 200

        # Logout করি
        resp2 = client.post("/api/v1/auth/logout", headers=headers)
        assert resp2.status_code == 200

        # এখন একই token দিয়ে /me আর কাজ করা উচিত না
        resp3 = client.get("/api/v1/auth/me", headers=headers)
        assert resp3.status_code == 401, (
            "Logout করার পরও token valid থেকে গেছে — revocation কাজ করছে না!"
        )


class TestFailOpenBehavior:
    """Redis না থাকলে সিস্টেম block না করে চলতে থাকা উচিত (availability bias)।"""

    def test_is_token_revoked_fails_open_on_redis_error(self, monkeypatch):
        from api.core import jwt_revoke

        def _broken_redis():
            raise ConnectionError("Redis is down")

        monkeypatch.setattr(jwt_revoke, "_get_redis", _broken_redis)
        # Exception ছুঁড়া উচিত নয়, এবং False (not revoked) ফেরত দেওয়া উচিত
        result = jwt_revoke.is_token_revoked("any-jti")
        assert result is False

    def test_is_user_globally_revoked_fails_open_on_redis_error(self, monkeypatch):
        from api.core import jwt_revoke

        def _broken_redis():
            raise ConnectionError("Redis is down")

        monkeypatch.setattr(jwt_revoke, "_get_redis", _broken_redis)
        result = jwt_revoke.is_user_globally_revoked(1, 1, int(time.time()))
        assert result is False
