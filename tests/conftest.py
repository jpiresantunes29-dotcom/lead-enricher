"""
Ajustes que valem para a suíte inteira.

O principal: **nenhum teste vai à rede buscar o JWKS do Supabase**. A
verificação de login consulta a chave pública do projeto, e deixar isso solto
tornaria a suíte lenta, dependente de internet e — pior — capaz de passar ou
falhar por motivo que não está no código. Quem testa o caminho do JWKS enche
o cache explicitamente (ver `tests/test_seguranca.py`).
"""
import pytest

from middleware import auth as auth_mod


@pytest.fixture(autouse=True)
def jwks_offline(monkeypatch):
    """Sem chaves publicadas, a não ser que o próprio teste coloque alguma."""
    auth_mod._jwks_cache["em"] = 0.0
    auth_mod._jwks_cache["chaves"] = []
    monkeypatch.setattr(auth_mod, "_jwks", lambda forcar=False: [])
    yield
    auth_mod._jwks_cache["em"] = 0.0
    auth_mod._jwks_cache["chaves"] = []
