"""Rotas HTTP, gate do PIN e tradução provedor -> serviço."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lgremote.api.app import create_app
from lgremote.catalog.tmdb import TmdbClient
from lgremote.config import Settings
from lgremote.tv.macros import TextStep
from lgremote.tv.session import TvSession
from tests.conftest import FakeWebOsClient

PIN = "4321"
HEADERS = {"X-Remote-Pin": PIN}

SEARCH_PAYLOAD = {
    "results": [
        {
            "id": 100088,
            "media_type": "tv",
            "name": "The Last of Us",
            "first_air_date": "2023-01-15",
            "poster_path": "/abc.jpg",
        }
    ]
}
PROVIDERS = {
    "results": {
        "BR": {
            "flatrate": [
                {"provider_id": 1899, "provider_name": "Max", "logo_path": "/m.jpg"},
                {"provider_id": 531, "provider_name": "Paramount Plus", "logo_path": "/p.jpg"},
            ]
        }
    }
}


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "tv_host": "10.0.0.5",
        "tv_client_key": "chave",
        "ui_pin": PIN,
        "tmdb_token": "",
        "tv_mac": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def tmdb_stub() -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search/multi"):
            return httpx.Response(200, json=SEARCH_PAYLOAD)
        return httpx.Response(200, json=PROVIDERS)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.themoviedb.org/3"
    )
    return TmdbClient("token", client=http)


@pytest.fixture
def fake_tv() -> FakeWebOsClient:
    return FakeWebOsClient("10.0.0.5")


@pytest.fixture
def presets_file(tmp_path: Path) -> Path:
    """Presets num arquivo temporário — o teste nunca toca no config/ real."""
    return tmp_path / "presets.yaml"


@pytest.fixture
def client(fake_tv: FakeWebOsClient, presets_file: Path) -> Iterator[TestClient]:
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(), session=session, presets_file=presets_file)
    with TestClient(app) as test_client:
        yield test_client


# --- PIN -----------------------------------------------------------------


def test_config_e_publica_para_o_pwa_montar_a_tela(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["paired"] is True


def test_sem_pin_nao_controla_a_tv(client: TestClient) -> None:
    assert client.post("/api/button", json={"name": "HOME"}).status_code == 401


def test_pin_errado_e_recusado(client: TestClient) -> None:
    response = client.post(
        "/api/button", json={"name": "HOME"}, headers={"X-Remote-Pin": "0000"}
    )
    assert response.status_code == 401


def test_servidor_sem_pin_configurado_recusa_tudo(fake_tv: FakeWebOsClient) -> None:
    """Melhor 503 explicando do que abrir a TV para a rede inteira."""
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(ui_pin=""), session=session)
    with TestClient(app) as test_client:
        response = test_client.post("/api/button", json={"name": "HOME"}, headers=HEADERS)
    assert response.status_code == 503
    assert "UI_PIN" in response.json()["detail"]


# --- controle ------------------------------------------------------------


def test_botao_chega_na_tv(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    assert client.post("/api/button", json={"name": "HOME"}, headers=HEADERS).status_code == 200
    assert fake_tv.buttons == ["HOME"]


def test_botao_invalido_e_400_e_nao_chega_na_tv(
    client: TestClient, fake_tv: FakeWebOsClient
) -> None:
    response = client.post("/api/button", json={"name": "rm -rf"}, headers=HEADERS)
    assert response.status_code == 400
    assert fake_tv.calls == []


def test_texto_digita_e_confirma(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    response = client.post("/api/text", json={"text": "Fallout", "enter": True}, headers=HEADERS)
    assert response.status_code == 200
    assert fake_tv.named("com.webos.service.ime/insertText") == [
        {"text": "Fallout", "replace": 1}
    ]
    assert fake_tv.named("com.webos.service.ime/sendEnterKey") == [None]


def test_status_traduz_app_atual_para_o_nome_do_servico(
    client: TestClient, fake_tv: FakeWebOsClient
) -> None:
    fake_tv.current = "com.wbd.stream"
    body = client.get("/api/status", headers=HEADERS).json()
    assert body["online"] is True
    assert body["current_service"] == "Max"


def test_ligar_sem_mac_configurado_explica_o_que_falta(client: TestClient) -> None:
    response = client.post("/api/power/on", headers=HEADERS)
    assert response.status_code == 400
    assert "TV_MAC" in response.json()["detail"]


def test_desligar_chega_na_tv(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    """Com TV_MAC configurado o PWA mandava sempre 'ligar' — a TV nunca desligava."""
    assert client.post("/api/power/off", headers=HEADERS).status_code == 200
    assert fake_tv.named("command:system/turnOff") == [None]


def test_desligar_funciona_em_webos_antigo(
    client: TestClient, fake_tv: FakeWebOsClient
) -> None:
    """webOS 3.x pode não popular o estado de energia; desligar não pode depender disso."""
    fake_tv.is_on = False
    assert client.post("/api/power/off", headers=HEADERS).status_code == 200
    assert fake_tv.named("command:system/turnOff") == [None]


def test_launch_de_servico_desconhecido_e_404(client: TestClient) -> None:
    assert (
        client.post("/api/launch", json={"service_id": "hulu"}, headers=HEADERS).status_code
        == 404
    )


# --- busca ---------------------------------------------------------------


def test_busca_desligada_sem_token(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "x"}, headers=HEADERS)
    assert response.status_code == 503
    assert "TMDB_TOKEN" in response.json()["detail"]


def test_busca_so_oferece_servicos_que_esta_tv_abre(fake_tv: FakeWebOsClient) -> None:
    """Paramount+ não está no apps.yaml: mostrar o botão seria prometer o que não cumpre."""
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(tmdb_token="t"), session=session, tmdb=tmdb_stub())
    with TestClient(app) as test_client:
        body = test_client.get("/api/search", params={"q": "last of us"}, headers=HEADERS).json()

    result = body["results"][0]
    assert result["name"] == "The Last of Us"
    assert [service["id"] for service in result["services"]] == ["max"]
    assert result["providers"] == ["Max", "Paramount Plus"]


# --- ação longa ----------------------------------------------------------


def test_abrir_titulo_responde_na_hora_e_reporta_progresso(client: TestClient) -> None:
    """Segurar a resposta ~20s faria o Safari desistir e o PWA parecer travado."""
    response = client.post(
        "/api/open",
        json={"service_id": "max", "title": "The Last of Us", "tmdb_id": 100088},
        headers=HEADERS,
    )
    assert response.status_code == 202

    action = client.get("/api/action", headers=HEADERS).json()
    assert action["status"] == "running"
    assert "The Last of Us" in action["label"]


def test_uma_acao_por_vez(client: TestClient) -> None:
    """Duas macros simultâneas mandariam teclas concorrentes e as duas falhariam."""
    payload = {"service_id": "max", "title": "Fallout"}
    assert client.post("/api/open", json=payload, headers=HEADERS).status_code == 202
    assert client.post("/api/open", json=payload, headers=HEADERS).status_code == 409


# --- gravador e presets --------------------------------------------------


def record_max(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    """Coloca o Max em foco (é ele que define de quem é o preset) e começa a gravar."""
    fake_tv.current = "com.wbd.stream"
    assert client.post("/api/record/start", headers=HEADERS).status_code == 200


def test_gravar_salvar_e_rodar(
    client: TestClient, fake_tv: FakeWebOsClient, presets_file: Path
) -> None:
    """O fluxo inteiro: é isto que substitui calibrar YAML na mão."""
    record_max(client, fake_tv)

    for button in ("DOWN", "DOWN", "ENTER"):
        client.post("/api/button", json={"name": button}, headers=HEADERS)

    saved = client.post("/api/record/stop", json={"label": "Dublado"}, headers=HEADERS)
    assert saved.status_code == 200
    assert saved.json()["preset"]["id"] == "dublado"
    assert presets_file.exists()

    fake_tv.calls.clear()
    started = client.post("/api/presets/max/dublado/run", headers=HEADERS)
    assert started.status_code == 202


def test_gravacao_sem_app_conhecido_e_recusada(
    client: TestClient, fake_tv: FakeWebOsClient
) -> None:
    """Sem saber a qual app pertence, o preset apareceria na hora errada."""
    fake_tv.current = "com.webos.app.livetv"
    response = client.post("/api/record/start", headers=HEADERS)
    assert response.status_code == 409
    assert "Abra o app" in response.json()["detail"]


def test_duas_gravacoes_ao_mesmo_tempo(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    record_max(client, fake_tv)
    assert client.post("/api/record/start", headers=HEADERS).status_code == 409


def test_status_expoe_a_gravacao(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    """O PWA reabre e precisa saber que ficou gravando — o estado mora no servidor."""
    record_max(client, fake_tv)
    client.post("/api/button", json={"name": "UP"}, headers=HEADERS)

    body = client.get("/api/status", headers=HEADERS).json()
    assert body["recording"] is True
    assert body["recording_presses"] == 1
    assert body["current_service_id"] == "max"


def test_botao_recusado_nao_entra_na_gravacao(
    client: TestClient, fake_tv: FakeWebOsClient
) -> None:
    """Gravar um toque que a TV nunca recebeu daria uma macro que não reproduz."""
    record_max(client, fake_tv)
    client.post("/api/button", json={"name": "LUNA_SHELL"}, headers=HEADERS)

    assert client.get("/api/record", headers=HEADERS).json()["presses"] == 0


def test_descartar_gravacao(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    record_max(client, fake_tv)
    client.post("/api/button", json={"name": "UP"}, headers=HEADERS)

    assert client.post("/api/record/cancel", headers=HEADERS).status_code == 200
    assert client.get("/api/record", headers=HEADERS).json()["recording"] is False


def test_parar_sem_toque_algum_avisa(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    record_max(client, fake_tv)
    response = client.post("/api/record/stop", json={"label": "Vazio"}, headers=HEADERS)
    assert response.status_code == 409
    assert "nenhum botão" in response.json()["detail"]


def test_presets_filtrados_por_servico(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    record_max(client, fake_tv)
    client.post("/api/button", json={"name": "UP"}, headers=HEADERS)
    client.post("/api/record/stop", json={"label": "Dublado"}, headers=HEADERS)

    todos = client.get("/api/presets", headers=HEADERS).json()["presets"]
    do_max = client.get("/api/presets", params={"service_id": "max"}, headers=HEADERS).json()
    do_disney = client.get(
        "/api/presets", params={"service_id": "disney"}, headers=HEADERS
    ).json()

    assert len(todos) == 1
    assert len(do_max["presets"]) == 1
    assert do_disney["presets"] == []


def test_apagar_preset(client: TestClient, fake_tv: FakeWebOsClient) -> None:
    record_max(client, fake_tv)
    client.post("/api/button", json={"name": "UP"}, headers=HEADERS)
    client.post("/api/record/stop", json={"label": "Dublado"}, headers=HEADERS)

    assert client.delete("/api/presets/max/dublado", headers=HEADERS).status_code == 200
    assert client.delete("/api/presets/max/dublado", headers=HEADERS).status_code == 404
    assert client.get("/api/presets", headers=HEADERS).json()["presets"] == []


def test_rodar_preset_inexistente(client: TestClient) -> None:
    assert client.post("/api/presets/max/nao-existe/run", headers=HEADERS).status_code == 404


def test_gravador_exige_pin(client: TestClient) -> None:
    assert client.post("/api/record/start").status_code == 401
    assert client.get("/api/presets").status_code == 401


# --- episódios -----------------------------------------------------------


def test_abrir_episodio_diz_o_que_vai_fazer(client: TestClient) -> None:
    response = client.post(
        "/api/open",
        json={"service_id": "max", "title": "Fallout", "season": 1, "episode": 4},
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert "T1E4" in response.json()["label"]


def test_abrir_episodio_sem_macro_avisa_antes(client: TestClient) -> None:
    """Melhor dizer agora do que você achar que travou esperando o episódio."""
    label = client.post(
        "/api/open",
        json={"service_id": "max", "title": "Fallout", "season": 1, "episode": 4},
        headers=HEADERS,
    ).json()["label"]

    from lgremote.config import APPS_FILE
    from lgremote.tv.apps import load_catalog

    if not load_catalog(APPS_FILE).by_id("max").can_pick_episode:
        assert "sem macro de episódio" in label


def test_episodio_fora_da_faixa_e_recusado(client: TestClient) -> None:
    response = client.post(
        "/api/open",
        json={"service_id": "max", "title": "X", "season": 1, "episode": 9999},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_episodios_sem_tmdb_configurado(client: TestClient) -> None:
    assert client.get("/api/seasons/100088", headers=HEADERS).status_code == 503
    assert client.get("/api/episodes/100088/1", headers=HEADERS).status_code == 503


def test_pwa_e_servido_na_raiz(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Controle da TV" in response.text
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/icons/icon-180.png").status_code == 200


# --- a TV falhando não é bug do servidor ---------------------------------


def _tv_recusa(fake_tv: FakeWebOsClient, presets_file: Path) -> TestClient:
    from aiowebostv import WebOsTvPairError

    fake_tv.connect_error = WebOsTvPairError("403 User denied access")
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(), session=session, presets_file=presets_file)
    return TestClient(app, raise_server_exceptions=False)


def test_chave_recusada_vira_502_com_o_motivo(
    fake_tv: FakeWebOsClient, presets_file: Path
) -> None:
    """Sem o handler isso saía como 500 genérico — e o celular dizia só "Erro 500"."""
    with _tv_recusa(fake_tv, presets_file) as client:
        response = client.post("/api/button", json={"name": "HOME"}, headers=HEADERS)

    assert response.status_code == 502
    assert "lgremote pair" in response.json()["detail"]


def test_status_offline_explica_o_porque(fake_tv: FakeWebOsClient, presets_file: Path) -> None:
    """O PWA mostra este texto: "TV desligada" esconderia um problema com conserto."""
    with _tv_recusa(fake_tv, presets_file) as client:
        payload = client.get("/api/status", headers=HEADERS).json()

    assert payload["online"] is False
    assert "lgremote pair" in payload["detail"]


def test_config_mostra_o_ip_que_a_sessao_esta_usando(
    fake_tv: FakeWebOsClient, presets_file: Path
) -> None:
    """Depois de a TV mudar de IP, o valor das settings está velho."""
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(), session=session, presets_file=presets_file)
    session.host = "192.168.2.117"

    with TestClient(app) as client:
        assert client.get("/api/config").json()["tv_host"] == "192.168.2.117"


# --- gravar macro do apps.yaml pelo celular ------------------------------


APPS_YAML = """\
# Comentário que explica como calibrar — não pode sumir numa regravação.
services:
  - id: max
    label: Max
    app_id: com.wbd.stream
    wait_after_launch: 12
    profile:
      - {button: ENTER}
    wait_before_profile: 14
    search:
      - {wait: 3}
      - {button: LEFT, times: 4}
      - {text: "{title}"}
    episode: []

shortcuts: [max]
"""


@pytest.fixture
def apps_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """apps.yaml de mentira: gravar macro escreve em disco, e o real não é cobaia."""
    from lgremote.api import app as api_app

    path = tmp_path / "apps.yaml"
    path.write_text(APPS_YAML, encoding="utf-8")
    monkeypatch.setattr(api_app, "APPS_FILE", path)
    return path


@pytest.fixture
def rec_client(
    fake_tv: FakeWebOsClient, presets_file: Path, apps_file: Path
) -> Iterator[TestClient]:
    fake_tv.current = "com.wbd.stream"  # Max em foco, como o gravador exige
    session = TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)
    app = create_app(settings(), session=session, presets_file=presets_file)
    with TestClient(app) as test_client:
        yield test_client


def test_grava_a_busca_no_apps_yaml(rec_client: TestClient, apps_file: Path) -> None:
    """O ciclo que substitui editar YAML na mão: navegar uma vez e salvar."""
    started = rec_client.post(
        "/api/record/start",
        json={"target": "search", "sample_title": "Fallout"},
        headers=HEADERS,
    )
    assert started.status_code == 200

    rec_client.post("/api/button", json={"name": "DOWN"}, headers=HEADERS)
    rec_client.post("/api/text", json={"text": "Fallout"}, headers=HEADERS)
    saved = rec_client.post("/api/record/stop", json={"label": ""}, headers=HEADERS)

    assert saved.status_code == 200
    assert saved.json()["target"] == "search"

    from lgremote.tv.apps import load_catalog as reload_catalog

    service = reload_catalog(apps_file).by_id("max")
    assert service is not None
    # O título digitado virou {title}: a macro serve para qualquer outra série.
    assert service.search[-1] == TextStep("{title}")
    assert "# Comentário que explica como calibrar" in apps_file.read_text(encoding="utf-8")


def test_macro_gravada_vale_na_hora(rec_client: TestClient) -> None:
    """Sem recarregar o catálogo, você testaria a macro antiga achando ser a nova."""
    rec_client.post("/api/record/start", json={"target": "profile"}, headers=HEADERS)
    rec_client.post("/api/button", json={"name": "RIGHT"}, headers=HEADERS)
    rec_client.post("/api/button", json={"name": "ENTER"}, headers=HEADERS)
    rec_client.post("/api/record/stop", json={"label": ""}, headers=HEADERS)

    estado = rec_client.get("/api/record", headers=HEADERS).json()
    assert estado["recording"] is False


def test_alvo_desconhecido_e_recusado(rec_client: TestClient) -> None:
    resposta = rec_client.post(
        "/api/record/start", json={"target": "wait_after_launch"}, headers=HEADERS
    )

    assert resposta.status_code == 400


def test_preset_ainda_exige_nome(rec_client: TestClient) -> None:
    """Macro do apps.yaml não tem nome; preset tem, e sem ele não dá para achá-lo."""
    rec_client.post("/api/record/start", json={"target": "preset"}, headers=HEADERS)
    rec_client.post("/api/button", json={"name": "DOWN"}, headers=HEADERS)

    resposta = rec_client.post("/api/record/stop", json={"label": ""}, headers=HEADERS)

    assert resposta.status_code == 400
