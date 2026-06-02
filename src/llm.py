"""
Capa de acceso al LLM.

Por ahora solo implementa Ollama local, pero la interfaz `LLMClient` está
pensada para que agregar otro backend sea cuestión de crear otra subclase — el
resto del agente no necesita cambiar.
"""

from __future__ import annotations

import os

import requests


class LLMError(RuntimeError):
    """Error al comunicarse con el backend del LLM."""


class LLMClient:
    """Interfaz mínima que el agente espera de cualquier backend."""

    def chat(self, system: str, user: str) -> str:
        raise NotImplementedError


class OllamaClient(LLMClient):
    """Cliente para un servidor Ollama local."""

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3.2")
        self.timeout = timeout

    def chat(self, system: str, user: str) -> str:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.0},  # determinismo para SQL
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise LLMError(
                f"No se pudo conectar a Ollama en {self.host}. "
                "¿Está corriendo `ollama serve`?"
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise LLMError(f"Ollama respondió con error: {exc}") from exc

        data = resp.json()
        try:
            return data["message"]["content"].strip()
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Respuesta inesperada de Ollama: {data}") from exc


def get_client() -> LLMClient:
    """Devuelve el cliente LLM configurado por variables de entorno.

    Hoy siempre devuelve Ollama. Si se agrega otro backend, este es el único
    lugar que decide cuál usar (por ejemplo, según una variable de entorno).
    """
    return OllamaClient()
