import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from starlette.requests import Request
from catalog_app.auth import _http_request

from catalog_app.auth import MiseAuth


class MiseAuthenticationTest(unittest.IsolatedAsyncioTestCase):
    async def test_claims_and_mise_fail_closed(self):
        mise = Mock()
        mise.configure.return_value = nullcontext(SimpleNamespace(error_code=0))
        mise.validate.return_value = nullcontext(
            SimpleNamespace(http_response_status_code=200)
        )
        module = SimpleNamespace(Mise=lambda: mise, MiseValidationInput=SimpleNamespace)

        with patch("catalog_app.auth.import_module", return_value=module):
            verifier = MiseAuth("tenant", "audience", "read", "https://example.com/mcp", client_id="client")

            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "headers": [],
                    "query_string": b"",
                    "scheme": "https",
                    "server": ("example.com", 443),
                }
            )
            context = _http_request.set(request)
            try:
                expiry = int(time.time()) + 3600
                delegated = jwt.encode(
                    {"exp": expiry, "azp": "user-client", "scp": "read", "sub": "user"},
                    "test-key-with-at-least-32-bytes-length",
                    algorithm="HS256",
                )
                user = await verifier.verify_token(delegated)
                self.assertEqual(user.client_id, "user-client")
                self.assertEqual(user.scopes, ["read"])
                self.assertEqual(user.claims["sub"], "user")
                self.assertEqual(user.token, delegated)
                validation_input = mise.validate.call_args.args[0]
                self.assertEqual(validation_input.original_method_header, "POST")
                self.assertEqual(
                    validation_input.original_uri_header, "https://example.com/mcp"
                )

                application = jwt.encode(
                    {
                        "exp": expiry,
                        "appid": "app-client",
                        "roles": ["read.Application"],
                    },
                    "test-key-with-at-least-32-bytes-length",
                    algorithm="HS256",
                )
                app = await verifier.verify_token(application)
                self.assertEqual(app.client_id, "app-client")
                self.assertEqual(app.scopes, ["read"])
                self.assertEqual(app.claims["roles"], ["read.Application"])

                mise.validate.return_value = nullcontext(
                    SimpleNamespace(http_response_status_code=401)
                )
                self.assertIsNone(await verifier.verify_token(delegated))
                mise.validate.side_effect = RuntimeError("MISE unavailable")
                self.assertIsNone(await verifier.verify_token(delegated))
            finally:
                _http_request.reset(context)

            mise.configure.return_value = nullcontext(SimpleNamespace(error_code=1))
            with self.assertRaisesRegex(RuntimeError, "MISE configuration failed"):
                MiseAuth("tenant", "audience", "read", "https://example.com/mcp", client_id="client")


if __name__ == "__main__":
    unittest.main()
