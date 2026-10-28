from axo import Axo, axo_method
from rory.core.security.cryptosystem.liu import Liu
from typing import List

class LiuCipher(Axo):
# class LiuCipher(object):
  def __init__(self,
    security_level:int=128,
    decimals:int = 2,
               seed = 1,
               _round:bool = False
  ):
    self.security_level = security_level
    self.secret_key_is_generated = False
    self.liu = Liu(
      _round = _round, 
      decimals=decimals, 
      secure_random = False, 
      seed=seed, 
      use_np_random= False, 
      security_level=self.security_level
    )
  
  @axo_method
  def generate_keys(self)->bool:
    if not self.secret_key_is_generated:
      self.secret_key_is_generated=True
      self.liu.generate_keys()
    return self.secret_key_is_generated
      
  @axo_method
  def encrypt_scalar(self,scalar:float)->List[float]:
    x = self.liu.encrypt_scalar(scalar)
    return x
  
  @axo_method
  def decrypt(self,ciphertext:List[float])->float:
    import numpy as np
    x = self.liu.decrypt_scalar(np.array(ciphertext)).data
    return float(x)


if __name__ == "__main__":
  liu = LiuCipher()
  liu.generate_keys()
  c = liu.encrypt_scalar(3.14).data
  print(c)
  d = liu.decrypt(c)
  print(d)
