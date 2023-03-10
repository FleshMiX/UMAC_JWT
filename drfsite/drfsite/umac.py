from functools import reduce

import rijndael
import struct

# Constants
MP64  = 0x01ffffff01ffffff                  # Polynomial key masks
MP128 = 0x01ffffff01ffffff01ffffff01ffffff
M32   = 0xffffffff                          # Bit masks
M64   = 0xffffffffffffffff
P36   = 0xffffffffb                         # Prime numbers
P64   = 0xffffffffffffffc5
P128  = 0xffffffffffffffffffffffffffffff61
T64   = 0xffffffff00000000                  # Polynomial test values
T128  = 0xffffffff000000000000000000000000

def nh(key,data,bitlength):
	a = 0
	for i in range(len(data)//8):
		for j in range(4):
			a += (((data[8*i+j  ] + key[8*i+j  ]) & M32) *
				  ((data[8*i+j+4] + key[8*i+j+4]) & M32))
	return (a+bitlength) & M64  # mod 2^^64

class umac:
	def __init__(self, umacKey, tagLength = 64):
		self.iters = iters = max(1, min(4,tagLength//32))
		# setup keys
		def kdf(kdfCipher, index, numbytes):
			ct = [ kdfCipher.encrypt('%s%s%s%s' % ('\x00' * 7, chr(index), '\x00' * 7, chr(i+1))) for i in range((numbytes+15)//16) ]
			return (''.join(ct))[ : numbytes]
		
		kdfCipher = rijndael.rijndeal(umacKey)
		self.pdfCipher = rijndael.rijndeal(kdf(kdfCipher,0,len(umacKey)))
		# L1Key is a sequence of tuples, each (32-bit * 256)
		L1Key = kdf(kdfCipher, 1, 1024 + (iters - 1) * 16)
		self.L1Key = [ struct.unpack('>256I', L1Key[16*i:16*i+1024]) for i in range(iters) ]
		# L2Key is a sequence of tuples, each (64-bit, 128-bit)
		L2Key = kdf(kdfCipher, 2, iters * 24)
		L2Key = [ struct.unpack('>3Q', L2Key[24*i:24*(i+1)]) for i in range(iters) ]
		self.L2Key = [ (L2Key[i][0] & MP64, ((L2Key[i][1] << 64) + L2Key[i][2]) & MP128) for i in range(iters) ]
		# L3Key is a sequence of tuples, each ( [64-bit * 8], 32-bit )
		tmp1 = kdf(kdfCipher, 3, iters * 64)
		tmp1 = [ struct.unpack('>8Q', tmp1[64*i:64*(i+1)]) for i in range(iters) ]
		tmp1 = [ map(lambda x : x % (2**36 - 5), i) for i in tmp1 ]
		tmp2 = kdf(kdfCipher, 4, iters * 4)
		tmp2 = struct.unpack('>%sI' % str(iters), tmp2)
		self.L3Key = zip(tmp1, tmp2)
		# Setup empty lists to accumulate L1Hash outputs
		self.L1Out = [ list() for i in range(iters) ] # A sequence of empty lists
		self.L3Out = list()
	
	def uhashUpdate(self, inString):
		data = struct.unpack('<256I', inString) # To big-endian, 256 * 32-bits
		for i in range(self.iters):
			self.L1Out[i].append(nh(self.L1Key[i], data, 8192))
	
	def uhashFinal(self, inString, bitlength):
		# Pad to 32-byte multiple and unpack to tuple of 32-bit values
		if len(inString) == 0: toAppend =  32
		else:                  toAppend = (32 - (len(inString) % 32)) % 32
		data = '%s%s' % (inString, '\x00' * toAppend)
		data = struct.unpack('<%sI' % str(len(data)//4), data)
		# Do three-level hash, iter times
		for i in range(self.iters):
			# L1 Hash
			self.L1Out[i].append(nh(self.L1Key[i], data, bitlength))
			# L2 Hash
			if len(self.L1Out[0]) == 1:
				L2Out = self.L1Out[i][0]
			else:
				loPoly = self.L1Out[i][ : 2**14]
				hiPoly = self.L1Out[i][2**14 : ]
				for j in range(len(loPoly)-1,-1,-1):
					if loPoly[j] >= T64:
						loPoly[j] = [P64-1, loPoly[j] - 59]
				L2Out = reduce((lambda x, y : (x * self.L2Key[i][0] + y) % P64), loPoly, 1)
				if (len(hiPoly) > 0):
					hiPoly.append(0x8000000000000000)
					if (len(hiPoly) % 2 == 1):
						hiPoly.append(0)
					hiPoly = [ (hiPoly[j] << 64) + hiPoly[j+1] for j in range(0,len(hiPoly),2) ]
					hiPoly.insert(0,L2Out)
					for j in range(len(hiPoly)-1,-1,-1):
						if hiPoly[j] >= T128:
							hiPoly[j] = [P128-1, hiPoly[j] - 159]
					L2Out = reduce(lambda x, y : (x * self.L2Key[i][1] + y) % P128, hiPoly, 1)
			#L3 Hash
			a,res = L2Out,0;
			for j in range(7,-1,-1):
				res += (a & 0xffff) * self.L3Key[i][0][j]
				a >>= 16
			self.L3Out.append(((res % P36) & M32) ^ self.L3Key[i][1])
	
	def umacUpdate(self, inString):
		self.uhashUpdate(inString)
	
	def umacFinal(self, inString, bitlength, nonce):
		self.uhashFinal(inString, bitlength)
		# Generate pad
		mask = [None, 3, 1, 0, 0]
		nlen = len(nonce)
		old = ord(nonce[nlen-1])
		idx = old & mask[self.iters]
		pt = '%s%s%s' % (nonce[0:nlen-1], chr(old - idx), '\x00' * (16-nlen))
		pad = struct.unpack('>4I', self.pdfCipher.encrypt(pt))
		result = [ hex(self.L3Out[i] ^ pad[self.iters*idx+i]) for i in range(self.iters) ]
		self.L1Out = [ list() for i in range(self.iters) ] # A sequence of empty lists
		self.L3Out = list()
		return result
		
u = umac('abcdefghijklmnop', 32)
x = 'a' * 2**25
n = 'bcdefghi'
i = 0
while ( (i+1) * 1024 < len(x) ):
	u.umacUpdate(x[i*1024:(i+1)*1024])
	i += 1
tag = u.umacFinal(x[i*1024:], 8*len(x[i*1024:]), n)
print(tag)

"""
Errata: 'a' * 2**25

linux> time python -OO umac.py
['0x85EE5CAEL']
13.100u 0.036s 0:13.32 98.5%    0+0k 0+0io 0pf+0w
linux> time python -OO umac.py
['0xFACA46F8L', '0x56E9B45FL']
26.293u 0.048s 0:26.81 98.2%    0+0k 0+0io 0pf+0w
linux> time python -OO umac.py
['0xA621C245L', '0x7C0012E6L', '0x4F3FDAE9L']
36.982u 0.032s 0:37.59 98.4%    0+0k 0+0io 0pf+0w
"""