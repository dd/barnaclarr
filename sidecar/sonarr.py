"""Read-only Sonarr/Radarr REST clients. Credentials never enter logs or state."""

import json
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .config import Config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ArrClient:
    """GET-only client for the shared *arr REST API (Sonarr and Radarr use /api/v3)."""

    def __init__(self, url: str, api_key, api_version: str):
        self.url, self.api_key, self.api_version = url, api_key, api_version

    def get(self, route: str, **params):
        url = f"{self.url.rstrip('/')}/api/{self.api_version}/{route}"
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"X-Api-Key": self.api_key()})
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)


def Sonarr(config: Config) -> ArrClient:
    return ArrClient(config.sonarr_url, config.api_key, config.api_version)


def Radarr(config: Config) -> ArrClient:
    return ArrClient(config.radarr_url, config.radarr_key, config.api_version)
