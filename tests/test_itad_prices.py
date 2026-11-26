import json
import unittest
from native_services.itad_prices import ITADClient, ITADError, _NoRedirect

class Response:
    def __init__(self, payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def read(self,n=-1): return self.payload[:n]

class ITADClientTests(unittest.TestCase):
    def test_redirect_handler_never_forwards_api_key(self):
        handler = _NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, 'Found', {}, 'https://attacker.example/'))

    def test_search_uses_header_and_encodes_title(self):
        seen=[]
        def open_(req,timeout):
            seen.append((req,timeout));return Response(b"[{\"id\":\"12345678-1234-1234-1234-123456789abc\",\"title\":\"Sea of Thieves\"}]")
        c=ITADClient("private-key-123",opener=open_)
        self.assertEqual(c.search("Sea of Thieves")[0]["title"],"Sea of Thieves")
        req,timeout=seen[0]
        self.assertEqual(req.get_header("Itad-api-key"),"private-key-123")
        self.assertIn("title=Sea+of+Thieves",req.full_url)
        self.assertEqual(timeout,8)
        self.assertNotIn("private-key-123",req.full_url)

    def test_prices_posts_ids_and_preserves_source_fields(self):
        seen=[]; payload={"id":"12345678-1234-1234-1234-123456789abc","current":{"shop":{"name":"Steam"},"price":{"amount":19.99,"currency":"USD"},"url":"https://store.example/item?aff=abc"}}
        def open_(req,timeout):
            seen.append(req);return Response(json.dumps([payload]).encode())
        game_id="12345678-1234-1234-1234-123456789abc"
        result=ITADClient("private-key-123",opener=open_).prices([game_id])
        req=seen[0]
        self.assertEqual(req.get_method(),"POST")
        self.assertEqual(json.loads(req.data),[game_id])
        self.assertIn("country=US",req.full_url)
        self.assertEqual(result,[payload])

    def test_prices_pass_market_and_voucher_choice_as_query_fields(self):
        seen=[]
        def open_(req,timeout):
            seen.append(req);return Response(b"[]")
        ITADClient("private-key-123",opener=open_).prices(
            ["12345678-1234-1234-1234-123456789abc"],country="CA",vouchers=True)
        self.assertIn("country=CA",seen[0].full_url)
        self.assertIn("vouchers=true",seen[0].full_url)

    def test_rejects_invalid_inputs_before_network(self):
        def forbidden(*a,**k): raise AssertionError("network must not be called")
        c=ITADClient("private-key-123",opener=forbidden)
        for title in ("", " "*300):
            with self.assertRaises(ValueError): c.search(title)
        with self.assertRaises(ValueError): c.prices(["bad"])
        with self.assertRaises(ValueError): c.prices(["12345678-1234-1234-1234-123456789abc"],country="USA")
        with self.assertRaises(ValueError): c.prices(["12345678-1234-1234-1234-123456789abc"],vouchers="yes")

    def test_rate_limit_error_does_not_leak_request_secret(self):
        class RateLimit(Exception): code=429
        def fail(*a,**k): raise RateLimit("url contains private-key-123")
        c=ITADClient("private-key-123",opener=fail)
        with self.assertRaises(ITADError) as err: c.search("test")
        self.assertEqual(str(err.exception),"ITAD rate limit reached; retry later.")
        self.assertNotIn("private-key-123",str(err.exception))

if __name__=="__main__": unittest.main()
