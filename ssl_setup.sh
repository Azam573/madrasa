#!/bin/bash
# ssl_setup.sh — SSL Certificate Setup
# Development: self-signed | Production: Let's Encrypt

MODE=${1:-"self-signed"}  # self-signed | letsencrypt
DOMAIN=${2:-"localhost"}

mkdir -p ssl

if [ "$MODE" = "self-signed" ]; then
    echo "🔐 Self-signed certificate তৈরি হচ্ছে..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout ssl/key.pem \
        -out ssl/cert.pem \
        -subj "/C=BD/ST=Dhaka/L=Dhaka/O=Smart Madrasa/CN=$DOMAIN" \
        -addext "subjectAltName=DNS:$DOMAIN,DNS:localhost,IP:127.0.0.1"
    echo "✅ ssl/cert.pem ও ssl/key.pem তৈরি হয়েছে"

elif [ "$MODE" = "letsencrypt" ]; then
    echo "🌐 Let's Encrypt certificate ($DOMAIN)..."
    docker run --rm \
        -v "$(pwd)/ssl:/etc/letsencrypt" \
        -v "$(pwd)/certbot-webroot:/var/www/certbot" \
        certbot/certbot certonly \
        --webroot -w /var/www/certbot \
        -d "$DOMAIN" \
        --email admin@$DOMAIN \
        --agree-tos --non-interactive
    # Copy certs
    cp ssl/live/$DOMAIN/fullchain.pem ssl/cert.pem
    cp ssl/live/$DOMAIN/privkey.pem   ssl/key.pem
    echo "✅ Let's Encrypt certificate সেটআপ সম্পন্ন"
fi

# Nginx SSL dir-এ copy
mkdir -p nginx-ssl
cp ssl/cert.pem nginx-ssl/
cp ssl/key.pem  nginx-ssl/
echo "✅ nginx-ssl/ ফোল্ডারে copy হয়েছে"
echo ""
echo "এখন চালান: docker-compose up -d nginx"
