"""
Cifra os segredos de CRM que ficaram em claro de antes da SECRETS_KEY existir.

Idempotente: rodar duas vezes não faz nada na segunda. Rode uma vez depois de
definir a variável em produção.

    SECRETS_KEY=... DATABASE_URL=... python scripts/recriptografar_segredos.py

Valor que já está cifrado é deixado como está — inclusive o que não abre com a
chave atual, porque reescrevê-lo apagaria a chance de recuperá-lo com a chave
antiga.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import SessionLocal          # noqa: E402
from services import crypto                       # noqa: E402


def main() -> int:
    if not crypto.chave_configurada():
        print(
            f"SECRETS_KEY ausente ou com menos de {crypto.TAMANHO_MINIMO} "
            'caracteres.\nGere com: python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"'
        )
        return 1

    db = SessionLocal()
    try:
        antes = crypto.inventario(db)
        print(f"Antes:  em claro={antes['em_claro']}  "
              f"cifrados={antes['cifrados']}  ilegíveis={antes['ilegiveis']}")

        resultado = crypto.recriptografar(db)
        db.commit()

        print(f"Cifrados agora: {resultado['convertidos']}")
        if resultado["ilegiveis"]:
            print(
                f"ATENÇÃO: {resultado['ilegiveis']} segredo(s) não abrem com "
                "esta chave e foram deixados intactos. Volte a chave anterior "
                "ou peça ao usuário para regravar a conexão."
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
