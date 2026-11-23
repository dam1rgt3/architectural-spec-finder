import json
import unittest
from http.server import ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

from app import ROOT, browse, connect, handler, rating_options, search
from demo import prepare


class SourceSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare()
        cls.base = ROOT / "data" / "demo"
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler(cls.base, None, True)
        )
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(5)
        cls.server.server_close()

    def setUp(self):
        self.db = connect(self.base)
        self.addCleanup(self.db.close)

    def test_ambiguous_glazing_needs_selection(self):
        rows = rating_options(self.db, "AFV/20 U-factor")["options"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("val" not in row for row in rows))
        for row in rows:
            self.assertTrue(row["variant"].startswith("AFV/20"))
            ref = self.db.execute(
                "SELECT val,y0,y1 FROM ratings WHERE id=?", (row["id"],)
            ).fetchone()
            self.assertEqual(
                ref["val"], "0.50" if "clear" in row["variant"] else "0.46"
            )
            self.assertLess(ref["y0"], ref["y1"])

    def test_wrong_model_never_substitutes_a_row(self):
        rows = rating_options(self.db, "AFV/201 U-factor")["options"]
        self.assertEqual(rows, [])

    def test_unrelated_query_returns_no_verified_rating(self):
        self.assertIsNone(rating_options(self.db, "U-factor of a wall panel"))

    def test_document_filter_and_citation(self):
        did = browse(self.db, "fixed", 0)["items"][0]["id"]
        results = search(self.db, "glazing", doc_id=did)["hits"]
        self.assertTrue(results)
        self.assertTrue(all(r["doc"] == did and r["page"] == 1 for r in results))

    def test_http_delivers_source_page_and_original(self):
        from PIL import Image

        with urlopen(self.origin + "/api/documents?q=venting") as r:
            doc = json.load(r)["items"][0]["id"]
        with urlopen(self.origin + "/api/page.png?doc=" + doc + "&page=1") as r:
            self.assertGreater(Image.open(BytesIO(r.read())).width, 400)
        with urlopen(self.origin + "/pdf/" + doc) as r:
            self.assertEqual(r.read(5), b"%PDF-")
        with self.assertRaises(HTTPError) as ctx:
            urlopen(self.origin + "/api/page.png?doc=" + doc + "&page=999")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
