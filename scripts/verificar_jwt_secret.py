"""
Confere, so localmente, se um SUPABASE_JWT_SECRET bate com um token real.

Nao manda nada para lugar nenhum - roda so na sua maquina. Uso:

    set SUPABASE_JWT_SECRET=cole-o-segredo-aqui        (PowerShell: $env:...)
    python scripts/verificar_jwt_secret.py "cole-o-access_token-aqui"

Se bater, o problema esta em outro lugar (nome/ambiente da variavel na
Vercel, por exemplo - o valor certo nao chegou ao servidor). Se nao bater,
o segredo copiado do Supabase esta errado ou e de outra chave.
"""
import os
import sys

from jose import JWTError, jwt

if len(sys.argv) != 2:
    print("Uso: python scripts/verificar_jwt_secret.py <access_token>")
    sys.exit(1)

token = sys.argv[1]
secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()

if not secret:
    print("SUPABASE_JWT_SECRET nao esta definida nesta sessao do terminal.")
    sys.exit(1)

try:
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
    print(f"BATEU. sub={payload.get('sub')} email={payload.get('email')} exp={payload.get('exp')}")
except JWTError as e:
    print(f"NAO BATEU: {type(e).__name__}: {e}")
