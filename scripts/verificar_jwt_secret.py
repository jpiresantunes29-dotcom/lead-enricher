"""
Confere, so localmente, se um SUPABASE_JWT_SECRET bate com um token real.

Nao manda nada para lugar nenhum - roda so na sua maquina. Uso:

    set SUPABASE_JWT_SECRET=cole-o-segredo-aqui        (PowerShell: $env:...)
    python scripts/verificar_jwt_secret.py "cole-o-access_token-aqui"

Se bater, o problema esta em outro lugar (nome/ambiente da variavel na
Vercel, por exemplo - o valor certo nao chegou ao servidor). Se nao bater,
o segredo copiado do Supabase esta errado ou e de outra chave.
"""
import base64
import os
import sys

from jose import JWTError, jwt

if len(sys.argv) != 2:
    print("Uso: python scripts/verificar_jwt_secret.py <access_token>")
    sys.exit(1)

token = sys.argv[1]
secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()

header = jwt.get_unverified_header(token)
print(f"Cabecalho do token (nao precisa de segredo): kid={header.get('kid')} alg={header.get('alg')}")

# O `iss` diz de qual projeto Supabase o token saiu. Se ele nao bater com o
# projeto que o frontend usa, o segredo certo nunca vai validar este token -
# o problema e o token (ou o segredo) ser de outro projeto, nao o segredo
# estar errado.
claims = jwt.get_unverified_claims(token)
print(f"Emissor do token (iss): {claims.get('iss')}")
print(f"Sub: {claims.get('sub')}  email: {claims.get('email')}  role: {claims.get('role')}")

if not secret:
    print("SUPABASE_JWT_SECRET nao esta definida nesta sessao do terminal.")
    sys.exit(1)


def tentar(nome, chave):
    try:
        payload = jwt.decode(token, chave, algorithms=["HS256"], options={"verify_aud": False})
        print(f"BATEU ({nome}). sub={payload.get('sub')} email={payload.get('email')} exp={payload.get('exp')}")
        return True
    except JWTError as e:
        print(f"NAO BATEU ({nome}): {type(e).__name__}: {e}")
        return False


ok = tentar("texto puro", secret)
if not ok:
    # Talvez o Supabase trate o segredo importado como base64url e decodifique
    # para bytes antes de assinar - tenta com padding ausente completado.
    padded = secret + "=" * (-len(secret) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        tentar("base64url decodificado", decoded)
    except Exception as e:
        print(f"(nao deu para decodificar como base64url: {e})")
