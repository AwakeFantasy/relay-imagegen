import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image
from scripts.image_client import ImageClient, ImagePayloadError, extract_image_bytes

def png_bytes():
    buf=io.BytesIO(); Image.new("RGB",(2,3),"red").save(buf,format="PNG"); return buf.getvalue()
class FakeUrlResponse:
    status=200
    def __init__(self,payload): self.payload=payload; self.headers={"Content-Type":"image/png"}
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.payload
class ImageClientTests(unittest.TestCase):
    def test_extracts_base64_payload(self):
        raw=png_bytes(); self.assertEqual(extract_image_bytes({"b64_json":base64.b64encode(raw).decode()}),raw)
    @patch("scripts.image_client.urlopen")
    def test_downloads_url_payload(self,urlopen):
        raw=png_bytes(); urlopen.return_value=FakeUrlResponse(raw); self.assertEqual(extract_image_bytes({"url":"https://example.test/image.png"}),raw); urlopen.assert_called_once()
    def test_rejects_empty_payload(self):
        with self.assertRaises(ImagePayloadError): extract_image_bytes({"b64_json":None,"url":None})
    @patch("scripts.image_client.OpenAI")
    def test_generate_uses_direct_sdk_and_extracts_response(self,openai_cls):
        raw=png_bytes(); openai_cls.return_value.images.generate.return_value.data=[type("Item",(),{"b64_json":base64.b64encode(raw).decode(),"url":None})()]
        client=ImageClient(api_key="key",base_url="https://relay.test/v1"); self.assertEqual(client.generate(model="gpt-image-2",prompt="x",size="1024x1024",quality="low"),[raw])
        openai_cls.return_value.images.generate.assert_called_once_with(model="gpt-image-2",prompt="x",size="1024x1024",quality="low")
if __name__=="__main__": unittest.main()
