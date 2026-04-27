import os
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gesturelink.certs")

def generate_self_signed_cert():
    cert_file = "cert.pem"
    key_file = "key.pem"
    
    if os.path.exists(cert_file) and os.path.exists(key_file):
        logger.info("✅ SSL Certificates already exist.")
        return

    logger.info("🛠️ Generating Self-Signed SSL Certificates...")
    try:
        # Generate private key and certificate
        # Valid for 365 days
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:4096", 
            "-keyout", key_file, "-out", cert_file, 
            "-days", "365", "-nodes",
            "-subj", "/C=US/ST=State/L=City/O=GestureLink/OU=Dev/CN=localhost"
        ]
        subprocess.run(cmd, check=True)
        logger.info(f"✅ Created {cert_file} and {key_file}")
    except Exception as e:
        logger.error(f"❌ Failed to generate certificates: {e}")
        logger.info("💡 Make sure 'openssl' is installed on your system.")

if __name__ == "__main__":
    generate_self_signed_cert()
