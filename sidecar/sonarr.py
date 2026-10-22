"""Read-only Sonarr REST client. Credentials never enter logs or state."""

import json
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .config import Config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Sonarr:
    def __init__(self, config: Config):
        self.config = config

    def get(self, route: str, **params):
        url = f"{self.config.sonarr_url.rstrip('/')}/api/{self.config.api_version}/{route}"
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"X-Api-Key": self.config.api_key_file.read_text().strip()})
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
