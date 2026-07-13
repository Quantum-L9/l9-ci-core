import httpx
import requests


def bad(payload):
    requests.post("http://worker-a:8000/v1/execute", json=payload)
    httpx.post("https://worker-b.internal/execute", json=payload)
