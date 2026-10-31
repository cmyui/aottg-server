"""Legacy client handshake compatibility, not a modern authenticated transport."""

import hashlib
import secrets

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .messages import ProtocolError

# RFC 2409 section 6.1 prime; the game SDK uses generator 22.
DH_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024"
    "E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD"
    "3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7E"
    "C6F44C42E9A63A3620FFFFFFFFFFFFFFFF",
    16,
)


def unsigned_bytes(number: int) -> bytes:
    return number.to_bytes((number.bit_length() + 7) // 8, "big")


class LegacyCipher:
    def __init__(self, remote_public_key: bytes) -> None:
        if not 1 <= len(remote_public_key) <= 96:
            raise ProtocolError("Invalid handshake key size")
        remote = int.from_bytes(remote_public_key, "big")
        if not 2 <= remote <= DH_PRIME - 2:
            raise ProtocolError("Invalid handshake public key")
        private = secrets.randbits(256) | 1
        self.public_key = unsigned_bytes(pow(22, private, DH_PRIME))
        shared_secret = unsigned_bytes(pow(remote, private, DH_PRIME))
        self._key = hashlib.sha256(shared_secret).digest()

    def decrypt(self, ciphertext: bytes) -> bytes:
        if not ciphertext or len(ciphertext) % 16:
            raise ProtocolError("Invalid encrypted message length")
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(bytes(16))).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        try:
            return unpadder.update(padded) + unpadder.finalize()
        except ValueError as exc:
            raise ProtocolError("Invalid encrypted message padding") from exc
