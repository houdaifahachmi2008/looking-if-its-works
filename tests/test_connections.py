import os

from viralbot import config, connections


def test_save_env_values_updates_in_place(tmp_path, monkeypatch):
    for k in ("YOUTUBE_API_KEY", "OLLAMA_MODEL", "NEW_KEY"):
        monkeypatch.setenv(k, "")  # registered so monkeypatch restores them after the test
    f = tmp_path / ".env"
    f.write_text("# comment\nYOUTUBE_API_KEY=\nLLM_PROVIDER=auto\n# OLLAMA_MODEL=\n")
    connections.save_env_values({"YOUTUBE_API_KEY": "AIzaTEST123456", "OLLAMA_MODEL": "qwen2.5:7b",
                                 "NEW_KEY": "x"}, path=f)
    text = f.read_text()
    assert "# comment" in text and "YOUTUBE_API_KEY=AIzaTEST123456" in text
    assert "OLLAMA_MODEL=qwen2.5:7b" in text and text.count("OLLAMA_MODEL") == 1
    assert text.strip().endswith("NEW_KEY=x")
    assert connections.read_env_file(f)["LLM_PROVIDER"] == "auto"
    assert os.environ["YOUTUBE_API_KEY"] == "AIzaTEST123456"
    assert config.env().youtube_api_key == "AIzaTEST123456"   # applied immediately
    if os.name != "nt":
        assert oct(f.stat().st_mode)[-3:] == "600"


def test_mask_and_missing_keys():
    assert connections.mask("") == "—"
    assert connections.mask("AIzaSyABCDEFGHIJKL") == "AIza…IJKL"
    assert connections.test_gemini("")[0] is False
    assert connections.test_twitch("", "")[0] is False
