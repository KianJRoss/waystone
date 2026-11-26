import unittest
from email.message import Message
from native_services.public_offer_scraper import parse_public_offer_snapshot, PublicPageScraper

STAMP="2026-11-18T12:00:00Z"
URL="https://shop.example/games/sample"

class Response:
    def __init__(self,body,url,content_type="text/plain"):
        self.body=body.encode();self.url=url;self.headers=Message();self.headers["Content-Type"]=content_type
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n=-1):return self.body[:n]
    def geturl(self):return self.url

class PublicOfferSnapshotTests(unittest.TestCase):
    def test_extracts_literal_offer_without_claiming_seller_class_or_verification(self):
        html='''<script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product","name":"Sample Game",
         "offers":{"@type":"Offer","price":"19.990","priceCurrency":"USD",
         "availability":"https://schema.org/InStock","seller":{"@type":"Organization","name":"Shop"}}}
        </script>'''
        rows=parse_public_offer_snapshot(html,page_url=URL,source_id="shop.example",observed_at=STAMP)
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row["price"],"19.990")
        self.assertEqual(row["currency"],"USD")
        self.assertEqual(row["seller"],"Shop")
        self.assertEqual(row["product_page_url"],URL)
        self.assertEqual(row["offer_class"],"unknown")
        self.assertEqual(row["verification"],"unverified")

    def test_handles_graph_and_offer_array_but_ignores_aggregates(self):
        html='''<script type="application/ld+json">{"@graph":[
        {"@type":"Product","name":"Game","offers":[
          {"@type":"Offer","price":0,"priceCurrency":"USD"},
          {"@type":"AggregateOffer","lowPrice":"1.00","priceCurrency":"USD"}]}]}</script>'''
        rows=parse_public_offer_snapshot(html,page_url=URL,source_id="shop",observed_at=STAMP)
        self.assertEqual([r["price"] for r in rows],["0"])

    def test_ignores_malformed_or_unusable_data_without_fabricating_zero(self):
        html='''<script type="application/ld+json">{broken}</script>
        <script type="application/ld+json">{"@type":"Product","name":"Game",
        "offers":{"@type":"Offer","price":"call for price","priceCurrency":"USD"}}</script>'''
        self.assertEqual(parse_public_offer_snapshot(html,page_url=URL,source_id="shop",observed_at=STAMP),[])

    def test_ignores_untyped_offer_records(self):
        html='''<script type="application/ld+json">{"@type":"Product","name":"Game","offers":{"price":"1.00","priceCurrency":"USD"}}</script>'''
        self.assertEqual(parse_public_offer_snapshot(html,page_url=URL,source_id="shop",observed_at=STAMP),[])

    def test_rejects_unsafe_page_urls_and_bad_source_ids(self):
        for url in ("file:///tmp/x", "https://user:pass@shop.example/p", "//shop.example/p"):
            with self.assertRaises(ValueError):
                parse_public_offer_snapshot("",page_url=url,source_id="shop",observed_at=STAMP)
        with self.assertRaises(ValueError):
            parse_public_offer_snapshot("",page_url=URL,source_id="../shop",observed_at=STAMP)

    def test_bounds_snapshot_and_timestamp(self):
        with self.assertRaises(ValueError):
            parse_public_offer_snapshot("x"*(2*1024*1024+1),page_url=URL,source_id="shop",observed_at=STAMP)
        with self.assertRaises(ValueError):
            parse_public_offer_snapshot("",page_url=URL,source_id="shop",observed_at="no-time")
        with self.assertRaises(ValueError):
            parse_public_offer_snapshot("",page_url=URL,source_id="shop",observed_at="2026-99-99T55:88:00Z")


class PublicPageFetcherTests(unittest.TestCase):
    def make(self,*,robots="User-agent: *\nAllow: /\n",page="<html>ok</html>",clock=None):
        seen=[]
        def opener(req,timeout):
            seen.append(req.full_url)
            if req.full_url.endswith("/robots.txt"):
                return Response(robots,req.full_url)
            return Response(page,req.full_url,"text/html")
        def resolver(host,port,type):
            return [(2,1,6,"",("93.184.216.34",443))]
        kwargs={"approved_hosts":{"shop.example"},"opener":opener,"resolver":resolver,"min_interval":0}
        if clock is not None:kwargs["clock"]=clock
        return PublicPageScraper(**kwargs),seen

    def test_fetches_only_approved_https_public_html_after_robots(self):
        scraper,seen=self.make()
        self.assertEqual(scraper.fetch_html(URL),"<html>ok</html>")
        self.assertEqual(seen,["https://shop.example/robots.txt",URL])

    def test_unapproved_host_private_dns_and_non_https_are_rejected_before_fetch(self):
        scraper,seen=self.make()
        for url in ("https://evil.example/item", "http://shop.example/item", "https://user:pass@shop.example/item"):
            with self.assertRaises(ValueError):scraper.fetch_html(url)
        self.assertEqual(seen,[])
        def private_resolver(host,port,type):return [(2,1,6,"",("127.0.0.1",443))]
        private=PublicPageScraper({"shop.example"},opener=lambda *a,**k:None,resolver=private_resolver)
        with self.assertRaises(ValueError):private.fetch_html(URL)

    def test_respects_robots_denial_and_does_not_fetch_product(self):
        scraper,seen=self.make(robots="User-agent: *\nDisallow: /games\n")
        with self.assertRaisesRegex(ValueError,"robots.txt disallows"):
            scraper.fetch_html(URL)
        self.assertEqual(seen,["https://shop.example/robots.txt"])

    def test_refuses_redirects_non_html_and_oversize_responses(self):
        def redirect(req,timeout):return Response("x","https://elsewhere.example/x","text/html")
        scraper=PublicPageScraper({"shop.example"},opener=redirect,resolver=lambda *a,**k:[(2,1,6,"",("93.184.216.34",443))],min_interval=0)
        with self.assertRaisesRegex(ValueError,"redirected the request"):
            scraper.fetch_html("https://shop.example/robots.txt")
        def binary(req,timeout):
            if req.full_url.endswith("robots.txt"):
                return Response("User-agent: *\nAllow: /\n",req.full_url)
            return Response("binary",req.full_url,"application/octet-stream")
        scraper=PublicPageScraper({"shop.example"},opener=binary,resolver=lambda *a,**k:[(2,1,6,"",("93.184.216.34",443))],min_interval=0)
        with self.assertRaisesRegex(ValueError,"did not return"):
            scraper.fetch_html(URL)
        def large(req,timeout):
            if req.full_url.endswith("robots.txt"):
                return Response("User-agent: *\nAllow: /\n",req.full_url)
            return Response("x"*1025,req.full_url,"text/html")
        scraper=PublicPageScraper({"shop.example"},opener=large,resolver=lambda *a,**k:[(2,1,6,"",("93.184.216.34",443))],min_interval=0,max_bytes=1024)
        with self.assertRaisesRegex(ValueError,"size limit"):
            scraper.fetch_html(URL)


    def test_production_transport_connects_to_validated_ip_not_a_second_dns_result(self):
        from unittest.mock import patch
        from email.message import Message
        calls=[]
        class FakeResponse:
            status=200
            def __init__(self,path):
                self.headers=Message();self.headers["Content-Type"]="text/plain" if path.endswith("robots.txt") else "text/html"
                self.path=path
            def read(self,n=-1):
                body="User-agent: *\nAllow: /\n" if self.path.endswith("robots.txt") else "<html>safe</html>"
                return body.encode()[:n]
        class FakeConnection:
            def __init__(self,host,address,timeout):
                calls.append(("connect",host,address))
                self.path=""
            def request(self,method,path,headers):
                calls.append(("request",method,path))
                self.path=path
            def getresponse(self):return FakeResponse(self.path)
            def close(self):pass
        def resolver(host,port,type):
            return [(2,1,6,"",("93.184.216.34",443))]
        scraper=PublicPageScraper({"shop.example"},resolver=resolver,min_interval=0)
        with patch("native_services.public_offer_scraper._PinnedHTTPSConnection",FakeConnection):
            self.assertEqual(scraper.fetch_html(URL),"<html>safe</html>")
        self.assertEqual(calls[0],("connect","shop.example","93.184.216.34"))
        self.assertEqual(calls[2],("connect","shop.example","93.184.216.34"))
        self.assertEqual([c[2] for c in calls if c[0]=="request"],["/robots.txt","/games/sample"])

    def test_obeys_per_host_rate_limit(self):
        now=[100.0]
        scraper,seen=self.make(clock=lambda:now[0])
        scraper._min_interval=60
        scraper.fetch_html(URL)
        with self.assertRaisesRegex(ValueError,"rate limit"):
            scraper.fetch_html("https://shop.example/games/other")
        self.assertEqual(seen,["https://shop.example/robots.txt",URL])

if __name__=="__main__":unittest.main()
