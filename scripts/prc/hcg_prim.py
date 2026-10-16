"""Примитивы сжатого B-rep PRC (ISO 14739-1 §11.12-11.16) поверх BitReader."""
import prc_bits

def uvar(r, n):  # UnsignedIntegerWithVariableBitNumber, MSB first
    return r.read_bits(n) if n > 0 else 0

def ivar(r, n):  # IntegerWithVariableBitNumber: sign bit (1 = negative) + n-1 bits
    neg = r.read_bit(); v = uvar(r, n - 1); return -v if neg else v

def dvar(r, tol, n):  # DoubleWithVariableBitNumber
    return ivar(r, n) * tol

def nbits_then_uint(r):  # NumberOfBitsThenUnsignedInteger
    return uvar(r, uvar(r, 5))

def entity_type(r):  # CompressedEntityType -> (is_curve, type)
    if r.read_bit():
        t = uvar(r, 2)
        return True, (t if t < 3 else (12 if uvar(r, 2) == 0 else 13))
    return False, uvar(r, 4)
