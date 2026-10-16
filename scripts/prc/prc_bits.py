import os
import re
import struct


def load_cofdoe(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r'\{(VT_double|VT_exponent),(\d+),(0x[0-9a-fA-F]+),\{DOUBLEWITHTWODWORDINTREE\((0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+)\)\}\}'
    matches = re.findall(pattern, content)
    result = {}
    for m in matches:
        type_str = m[0]
        nbits = int(m[1])
        bits = int(m[2], 16)
        upper = int(m[3], 16)
        lower = int(m[4], 16)
        key = (nbits, bits)
        if key not in result:
            is_double = (type_str == 'VT_double')
            value_bytes = struct.pack('<II', lower, upper)
            result[key] = (is_double, value_bytes)
    return result


_COFDOE = load_cofdoe(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor', 'PRCdouble.cc'))


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.n = len(data) * 8

    def read_bit(self) -> int:
        if self.pos >= self.n:
            raise EOFError(f"end of data at bit {self.pos}")
        bit = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return bit

    def read_bits(self, k) -> int:
        result = 0
        for _ in range(k):
            result = (result << 1) | self.read_bit()
        return result

    def read_char(self) -> int:
        return self.read_bits(8)

    def read_uint(self) -> int:
        result = 0
        count = 0
        while self.read_bit():
            result |= self.read_char() << (8 * count)
            count += 1
        return result

    def read_int(self) -> int:
        result = 0
        count = 0
        while self.read_bit():
            result |= self.read_char() << (8 * count)
            count += 1
        if count > 0 and count < 4:
            if result & (1 << (8 * count - 1)):
                result -= 1 << (8 * count)
        elif count == 4:
            if result & 0x80000000:
                result -= 1 << 32
        return result

    def read_bool(self) -> bool:
        return bool(self.read_bit())

    def read_string(self) -> str:
        if not self.read_bit():
            return ""
        n = self.read_uint()
        byte_list = [self.read_char() for _ in range(n)]
        return bytes(byte_list).decode('utf-8', errors='replace')

    def read_double(self) -> float:
        ucofdoe = 0
        pcofdoe = None
        for i in range(1, 23):
            ucofdoe = (ucofdoe << 1) | self.read_bit()
            key = (i, ucofdoe)
            if key in _COFDOE:
                pcofdoe = _COFDOE[key]
                break
        if pcofdoe is None:
            raise ValueError(f"bad double code at bit {self.pos}")
        
        is_double, value_bytes = pcofdoe
        v = bytearray(value_bytes)
        
        # Check for zero
        if i == 2 and ucofdoe == 1 and is_double:
            return struct.unpack('<d', bytes(v))[0]
        
        # Sign
        sign_bit = self.read_bit()
        if sign_bit:
            v[7] |= 0x80
        else:
            v[7] &= 0x7F
        
        if is_double:
            return struct.unpack('<d', bytes(v))[0]
        
        # Mantissa flag
        if self.read_bit() == 0:
            return struct.unpack('<d', bytes(v))[0]
        
        # b4
        b4 = 0
        for _ in range(4):
            b4 = (b4 << 1) | self.read_bit()
        v[6] |= b4
        
        lastByte = 0
        currentByte = 5
        
        while currentByte >= lastByte:
            if self.read_bit():
                v[currentByte] = self.read_char()
            else:
                offset = self.read_bits(3)
                if offset == 0:
                    pByte = v[currentByte + 1]
                    while currentByte >= lastByte:
                        v[currentByte] = pByte
                        currentByte -= 1
                    break
                elif offset == 6:
                    pByte = v[currentByte + 1]
                    lastByte = 1
                    while currentByte >= lastByte:
                        v[currentByte] = pByte
                        currentByte -= 1
                    v[currentByte] = self.read_char()
                    break
                else:
                    v[currentByte] = v[currentByte + offset]
            currentByte -= 1
            
        return struct.unpack('<d', bytes(v))[0]

    def tell(self) -> int:
        return self.pos


if __name__ == '__main__':
    assert len(_COFDOE) > 2000
    assert (2, 1) in _COFDOE
    is_double, value_bytes = _COFDOE[(2, 1)]
    assert is_double is True
    assert struct.unpack('<d', value_bytes)[0] == 0.0
    print("ok")
