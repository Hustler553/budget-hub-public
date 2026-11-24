import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12

SEALED_PREFIX_V1 = "sealed:v1:"
SEALED_PREFIX = "sealed:v2:"
_PREFIXES = (SEALED_PREFIX, SEALED_PREFIX_V1)

KEY_ENV = "BUDGET_VAULT_KEY"
LEGACY_KEY_ENV = "BUDGET_PLAID_KEY"

SALT_SETTING = "vault_salt"
SALT_BYTES = 16

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

_KEY_CACHE = {}


class NoKey(RuntimeError):
    pass


def key_material():
    from budget import config

    return config.VAULT_KEY or config.PLAID_KEY or ""


def configured():
    return bool(key_material())


def _salt(create=True):
    from budget import db

    stored = db.get_setting(SALT_SETTING, "")
    if stored:
        return base64.b64decode(stored)
    if not create:
        return None
    salt = os.urandom(SALT_BYTES)
    db.set_setting(SALT_SETTING, base64.b64encode(salt).decode())
    return salt


def _v1_keys():
    from budget import config

    seen, keys = set(), []
    for material in (config.VAULT_KEY, config.PLAID_KEY):
        if material and material not in seen:
            seen.add(material)
            keys.append(hashlib.sha256(material.encode()).digest())
    return keys


def _open_v1(nonce, ciphertext):
    keys = _v1_keys()
    if not keys:
        raise NoKey(f"{KEY_ENV} is not set, so secrets cannot be sealed or read")
    for index, key in enumerate(keys):
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
        except InvalidTag:
            if index == len(keys) - 1:
                raise
    raise AssertionError("unreachable: the loop returns or raises")


def _key(create_salt=True):
    material = key_material()
    if not material:
        raise NoKey(f"{KEY_ENV} is not set, so secrets cannot be sealed or read")
    salt = _salt(create=create_salt)
    if salt is None:
        raise NoKey("this database holds no vault salt, so nothing was sealed here")
    cached = (material, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    if cached not in _KEY_CACHE:
        _KEY_CACHE[cached] = hashlib.scrypt(
            material.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
            dklen=32, maxmem=64 * 1024 * 1024)
    return _KEY_CACHE[cached]


MIN_KEY_CHARS = 32
_MIN_DISTINCT = 10


def warn_if_key_is_weak(out=None):
    import sys

    material = key_material()
    if not material:
        return None
    problem = None
    if len(material) < MIN_KEY_CHARS:
        problem = (f"only {len(material)} characters long (want at least "
                   f"{MIN_KEY_CHARS})")
    elif len(set(material)) < _MIN_DISTINCT:
        problem = (f"built from only {len(set(material))} distinct characters, "
                   f"so it is far less random than its length suggests")
    if problem is None:
        return None
    message = (
        f"  NOTE: the vault key is {problem}.\n"
        f"  Secrets are stretched with scrypt now, which makes each guess\n"
        f"  expensive -- but a key this short has few enough candidates that\n"
        f"  it can still be ground out against any copy of the database.\n"
        f"  Generate one with: python -c \"import secrets; "
        f"print(secrets.token_urlsafe(32))\"\n"
        f"  Changing it re-seals nothing automatically: re-enter each secret\n"
        f"  afterwards, and re-link any bank whose token will not open."
    )
    print(message, file=out or sys.stderr)
    return message


def _aad(slot):
    if not slot:
        raise ValueError("a sealed value must name the slot it belongs to")
    return slot.encode("utf-8")


def seal_bytes(plaintext, slot):
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), _aad(slot))


def open_bytes(blob, slot):
    if blob is None:
        return None, None
    nonce, ciphertext = bytes(blob[:NONCE_BYTES]), bytes(blob[NONCE_BYTES:])
    try:
        return AESGCM(_key(create_salt=False)).decrypt(
            nonce, ciphertext, _aad(slot)).decode(), 2
    except (InvalidTag, NoKey):
        return _open_v1(nonce, ciphertext), 1


def unseal_bytes(blob, slot):
    return open_bytes(blob, slot)[0]


def seal_text(plaintext, slot):
    if plaintext is None or plaintext == "":
        return plaintext
    return SEALED_PREFIX + base64.b64encode(seal_bytes(plaintext, slot)).decode()


def is_sealed(stored):
    return isinstance(stored, str) and stored.startswith(_PREFIXES)


def is_current(stored):
    return isinstance(stored, str) and stored.startswith(SEALED_PREFIX)


def unseal_text(stored, slot):
    if not is_sealed(stored):
        return stored
    version2 = is_current(stored)
    prefix = SEALED_PREFIX if version2 else SEALED_PREFIX_V1
    raw = base64.b64decode(stored[len(prefix):])
    nonce, ciphertext = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        if version2:
            return AESGCM(_key(create_salt=False)).decrypt(
                nonce, ciphertext, _aad(slot)).decode()
        return _open_v1(nonce, ciphertext)
    except InvalidTag:
        raise NoKey("this secret was sealed with a different key, "
                    "or does not belong in this setting") from None
