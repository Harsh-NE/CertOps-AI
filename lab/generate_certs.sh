#!/usr/bin/env bash

# ==============================================================================
# CertOps AI - Certificate Lab Generator
# ==============================================================================
#
# Generates exactly 5 test certificates:
#
#   app1.lab.local -> HEALTHY / PERFECT
#   app2.lab.local -> EXPIRING IN 3 DAYS
#   app3.lab.local -> ALREADY EXPIRED
#   app4.lab.local -> BROKEN CHAIN
#   app5.lab.local -> VALID CERTIFICATE / NO OWNER
#
# Output:
#
# certs/
# ├── ca/
# │   ├── rootCA.key
# │   └── rootCA.pem
# │
# ├── intermediate/
# │   ├── intermediateCA.key
# │   ├── intermediateCA.csr
# │   └── intermediateCA.pem
# │
# ├── app1/
# │   ├── privkey.pem
# │   ├── req.csr
# │   ├── cert.pem
# │   ├── fullchain.pem
# │   └── metadata.json
# │
# ├── app2/
# ├── app3/
# ├── app4/
# ├── app5/
# │
# └── inventory.json
#
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Locate script and certificate directory
# ------------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"

cd "$CERT_DIR"

ROOT_DAYS=3650
INTERMEDIATE_DAYS=1825

echo
echo "============================================================"
echo " CertOps AI - Certificate Lab Generator"
echo "============================================================"
echo

# ==============================================================================
# 1. CLEAN PREVIOUS LAB
# ==============================================================================

echo "[1/8] Cleaning previous certificates..."

rm -rf \
    ca \
    intermediate \
    app1 \
    app2 \
    app3 \
    app4 \
    app5 \
    broken-ca \
    inventory.json \
    nginx-rootCA.pem

mkdir -p \
    ca \
    intermediate \
    app1 \
    app2 \
    app3 \
    app4 \
    app5 \
    broken-ca

# ==============================================================================
# 2. ROOT CA
# ==============================================================================

echo
echo "[2/8] Generating Root CA..."

openssl genrsa \
    -out ca/rootCA.key \
    4096

openssl req \
    -x509 \
    -new \
    -nodes \
    -key ca/rootCA.key \
    -sha256 \
    -days "$ROOT_DAYS" \
    -subj "/C=US/O=CertPlatform Lab/CN=CertPlatform Lab Root CA" \
    -out ca/rootCA.pem

echo "      Root CA created."

# ==============================================================================
# 3. INTERMEDIATE CA
# ==============================================================================

echo
echo "[3/8] Generating Intermediate CA..."

openssl genrsa \
    -out intermediate/intermediateCA.key \
    4096

openssl req \
    -new \
    -key intermediate/intermediateCA.key \
    -subj "/C=US/O=CertPlatform Lab/CN=CertPlatform Lab Intermediate CA" \
    -out intermediate/intermediateCA.csr

cat > intermediate/ext.cnf <<'EOF'
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

openssl x509 \
    -req \
    -in intermediate/intermediateCA.csr \
    -CA ca/rootCA.pem \
    -CAkey ca/rootCA.key \
    -CAcreateserial \
    -days "$INTERMEDIATE_DAYS" \
    -sha256 \
    -extfile intermediate/ext.cnf \
    -out intermediate/intermediateCA.pem

echo "      Intermediate CA created."

# ==============================================================================
# HELPER: GENERATE NORMAL LEAF
# ==============================================================================

generate_leaf() {

    local NAME="$1"
    local CN="$2"
    local DAYS="$3"
    local SAN="$4"
    local OWNER="$5"
    local SCENARIO="$6"

    echo
    echo "      Generating $NAME ($CN)..."

    # --------------------------------------------------------------------------
    # Private key
    # --------------------------------------------------------------------------

    openssl genrsa \
        -out "$NAME/privkey.pem" \
        2048

    # --------------------------------------------------------------------------
    # CSR
    # --------------------------------------------------------------------------

    openssl req \
        -new \
        -key "$NAME/privkey.pem" \
        -subj "/C=US/O=CertPlatform Lab/CN=$CN" \
        -out "$NAME/req.csr"

    # --------------------------------------------------------------------------
    # Certificate extensions
    # --------------------------------------------------------------------------

    cat > "$NAME/ext.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

    # --------------------------------------------------------------------------
    # Sign with legitimate Intermediate CA
    # --------------------------------------------------------------------------

    openssl x509 \
        -req \
        -in "$NAME/req.csr" \
        -CA intermediate/intermediateCA.pem \
        -CAkey intermediate/intermediateCA.key \
        -CAcreateserial \
        -days "$DAYS" \
        -sha256 \
        -extfile "$NAME/ext.cnf" \
        -out "$NAME/cert.pem"

    # --------------------------------------------------------------------------
    # Full chain
    # --------------------------------------------------------------------------

    cat \
        "$NAME/cert.pem" \
        intermediate/intermediateCA.pem \
        > "$NAME/fullchain.pem"

    # --------------------------------------------------------------------------
    # Backend test metadata
    #
    # expected_scenario is ONLY test metadata.
    # The backend must independently discover the actual state.
    # --------------------------------------------------------------------------

    cat > "$NAME/metadata.json" <<EOF
{
  "service_name": "$NAME",
  "hostname": "$CN",
  "environment": "lab",
  "cloud": "local",
  "certificate": {
    "cert_path": "cert.pem",
    "fullchain_path": "fullchain.pem",
    "private_key_path": "privkey.pem"
  },
  "owner": $OWNER,
  "expected_scenario": "$SCENARIO"
}
EOF

    echo "      Created: $NAME/fullchain.pem"
}

# ==============================================================================
# 4. APP1 - HEALTHY
# ==============================================================================

echo
echo "[4/8] Generating APP1 - HEALTHY..."

generate_leaf \
    "app1" \
    "app1.lab.local" \
    365 \
    "DNS:app1.lab.local" \
    '"platform-team"' \
    "healthy"

# ==============================================================================
# 5. APP2 - EXPIRING IN 3 DAYS
# ==============================================================================

echo
echo "[5/8] Generating APP2 - EXPIRING IN 3 DAYS..."

generate_leaf \
    "app2" \
    "app2.lab.local" \
    3 \
    "DNS:app2.lab.local" \
    '"platform-team"' \
    "expiring"

# ==============================================================================
# 6. APP3 - ALREADY EXPIRED
# ==============================================================================
#
# We use openssl ca because it supports explicit start/end dates.
#
# Certificate:
#
#   notBefore = 30 days ago
#   notAfter  = 1 day ago
#
# ==============================================================================

echo
echo "[6/8] Generating APP3 - ALREADY EXPIRED..."

openssl genrsa \
    -out app3/privkey.pem \
    2048

openssl req \
    -new \
    -key app3/privkey.pem \
    -subj "/C=US/O=CertPlatform Lab/CN=app3.lab.local" \
    -out app3/req.csr

mkdir -p app3/newcerts

touch app3/index.txt

echo "1000" > app3/serial

cat > app3/openssl-ca.cnf <<EOF
[ ca ]
default_ca = CA_default

[ CA_default ]

database = $CERT_DIR/app3/index.txt
serial = $CERT_DIR/app3/serial
new_certs_dir = $CERT_DIR/app3/newcerts

certificate = $CERT_DIR/intermediate/intermediateCA.pem
private_key = $CERT_DIR/intermediate/intermediateCA.key

default_md = sha256
default_days = 365

policy = policy_any

x509_extensions = leaf_extensions

[ policy_any ]

commonName = supplied
countryName = optional
organizationName = optional

[ leaf_extensions ]

basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:app3.lab.local
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

START_DATE="$(date -u -d '30 days ago' '+%Y%m%d%H%M%SZ')"
END_DATE="$(date -u -d '1 day ago' '+%Y%m%d%H%M%SZ')"

openssl ca \
    -config app3/openssl-ca.cnf \
    -in app3/req.csr \
    -out app3/cert.pem \
    -startdate "$START_DATE" \
    -enddate "$END_DATE" \
    -batch

cat \
    app3/cert.pem \
    intermediate/intermediateCA.pem \
    > app3/fullchain.pem

cat > app3/metadata.json <<'EOF'
{
  "service_name": "app3",
  "hostname": "app3.lab.local",
  "environment": "lab",
  "cloud": "local",
  "certificate": {
    "cert_path": "cert.pem",
    "fullchain_path": "fullchain.pem",
    "private_key_path": "privkey.pem"
  },
  "owner": "platform-team",
  "expected_scenario": "expired"
}
EOF

echo "      APP3 validity:"
openssl x509 \
    -in app3/cert.pem \
    -noout \
    -dates

# ==============================================================================
# 7. APP4 - BROKEN CHAIN
# ==============================================================================
#
# APP4 is signed by an unrelated CA.
#
# But its fullchain contains our normal CertPlatform Intermediate CA.
#
# Therefore:
#
#   app4
#      ↓
#   CertPlatform Intermediate
#
# is NOT cryptographically valid.
#
# ==============================================================================

echo
echo "[7/8] Generating APP4 - BROKEN CHAIN..."

# ------------------------------------------------------------------------------
# Unrelated CA
# ------------------------------------------------------------------------------

openssl genrsa \
    -out broken-ca/brokenCA.key \
    2048

openssl req \
    -x509 \
    -new \
    -nodes \
    -key broken-ca/brokenCA.key \
    -sha256 \
    -days 3650 \
    -subj "/C=US/O=Untrusted Lab/CN=Untrusted Broken Root CA" \
    -out broken-ca/brokenCA.pem

# ------------------------------------------------------------------------------
# APP4 key
# ------------------------------------------------------------------------------

openssl genrsa \
    -out app4/privkey.pem \
    2048

openssl req \
    -new \
    -key app4/privkey.pem \
    -subj "/C=US/O=CertPlatform Lab/CN=app4.lab.local" \
    -out app4/req.csr

cat > app4/ext.cnf <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:app4.lab.local
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

# ------------------------------------------------------------------------------
# Sign APP4 with unrelated CA
# ------------------------------------------------------------------------------

openssl x509 \
    -req \
    -in app4/req.csr \
    -CA broken-ca/brokenCA.pem \
    -CAkey broken-ca/brokenCA.key \
    -CAcreateserial \
    -days 365 \
    -sha256 \
    -extfile app4/ext.cnf \
    -out app4/cert.pem

# ------------------------------------------------------------------------------
# DELIBERATELY WRONG FULLCHAIN
# ------------------------------------------------------------------------------

cat \
    app4/cert.pem \
    intermediate/intermediateCA.pem \
    > app4/fullchain.pem

cat > app4/metadata.json <<'EOF'
{
  "service_name": "app4",
  "hostname": "app4.lab.local",
  "environment": "lab",
  "cloud": "local",
  "certificate": {
    "cert_path": "cert.pem",
    "fullchain_path": "fullchain.pem",
    "private_key_path": "privkey.pem"
  },
  "owner": "security-team",
  "expected_scenario": "broken_chain"
}
EOF

echo "      APP4 deliberately has a broken chain."

# ==============================================================================
# 8. APP5 - VALID CERTIFICATE / NO OWNER
# ==============================================================================

echo
echo "[8/8] Generating APP5 - VALID CERTIFICATE / NO OWNER..."

generate_leaf \
    "app5" \
    "app5.lab.local" \
    365 \
    "DNS:app5.lab.local" \
    "null" \
    "no_owner"

# ==============================================================================
# ROOT CA FOR NGINX
# ==============================================================================

cp \
    ca/rootCA.pem \
    nginx-rootCA.pem

# ==============================================================================
# CREATE INVENTORY
# ==============================================================================

cat > inventory.json <<'EOF'
{
  "lab": "certops-ai",
  "environment": "local",
  "services": [
    {
      "service_name": "app1",
      "hostname": "app1.lab.local",
      "certificate_path": "app1/cert.pem",
      "fullchain_path": "app1/fullchain.pem",
      "owner": "platform-team",
      "expected_scenario": "healthy"
    },
    {
      "service_name": "app2",
      "hostname": "app2.lab.local",
      "certificate_path": "app2/cert.pem",
      "fullchain_path": "app2/fullchain.pem",
      "owner": "platform-team",
      "expected_scenario": "expiring"
    },
    {
      "service_name": "app3",
      "hostname": "app3.lab.local",
      "certificate_path": "app3/cert.pem",
      "fullchain_path": "app3/fullchain.pem",
      "owner": "platform-team",
      "expected_scenario": "expired"
    },
    {
      "service_name": "app4",
      "hostname": "app4.lab.local",
      "certificate_path": "app4/cert.pem",
      "fullchain_path": "app4/fullchain.pem",
      "owner": "security-team",
      "expected_scenario": "broken_chain"
    },
    {
      "service_name": "app5",
      "hostname": "app5.lab.local",
      "certificate_path": "app5/cert.pem",
      "fullchain_path": "app5/fullchain.pem",
      "owner": null,
      "expected_scenario": "no_owner"
    }
  ]
}
EOF

# ==============================================================================
# VERIFICATION
# ==============================================================================

echo
echo "============================================================"
echo " Certificate Verification"
echo "============================================================"

echo
echo "APP1:"
openssl x509 \
    -in app1/cert.pem \
    -noout \
    -subject \
    -issuer \
    -dates

echo
echo "APP2:"
openssl x509 \
    -in app2/cert.pem \
    -noout \
    -subject \
    -issuer \
    -dates

echo
echo "APP3:"
openssl x509 \
    -in app3/cert.pem \
    -noout \
    -subject \
    -issuer \
    -dates

echo
echo "APP4:"
openssl x509 \
    -in app4/cert.pem \
    -noout \
    -subject \
    -issuer \
    -dates

echo
echo "APP5:"
openssl x509 \
    -in app5/cert.pem \
    -noout \
    -subject \
    -issuer \
    -dates

echo
echo "============================================================"
echo " Lab generation completed."
echo "============================================================"
echo
echo "Scenarios:"
echo
echo "  app1.lab.local -> HEALTHY"
echo "  app2.lab.local -> EXPIRING IN 3 DAYS"
echo "  app3.lab.local -> ALREADY EXPIRED"
echo "  app4.lab.local -> BROKEN CHAIN"
echo "  app5.lab.local -> VALID / NO OWNER"
echo
echo "Inventory:"
echo "  $CERT_DIR/inventory.json"
echo
echo "Root CA:"
echo "  $CERT_DIR/ca/rootCA.pem"
echo
echo "============================================================"