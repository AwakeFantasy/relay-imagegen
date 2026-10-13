import base64
import io
import os
import tempfile
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

    @patch("scripts.image_client.OpenAI")
    def test_generate_stream_saves_partial_callback_and_returns_completed_image(self,openai_cls):
        partial=png_bytes(); final=png_bytes()
        events=[
            {"type":"image_generation.partial_image","partial_image_index":0,"b64_json":base64.b64encode(partial).decode()},
            {"type":"image_generation.completed","b64_json":base64.b64encode(final).decode()},
        ]
        openai_cls.return_value.images.generate.return_value=iter(events)
        client=ImageClient(api_key="key",base_url="https://relay.test/v1")
        previews=[]
        result=client.generate_stream(model="gpt-image-2.5-flare",prompt="x",size="1024x1024",quality="high",partial_images=1,on_partial=lambda index,data:previews.append((index,data)))
        self.assertEqual(result,[final])
        self.assertEqual(previews,[(0,partial)])
        openai_cls.return_value.images.generate.assert_called_once_with(model="gpt-image-2.5-flare",prompt="x",size="1024x1024",quality="high",stream=True,partial_images=1)

    @patch("scripts.image_client.OpenAI")
    def test_generate_stream_requires_completed_event(self,openai_cls):
        openai_cls.return_value.images.generate.return_value=iter([])
        client=ImageClient(api_key="key",base_url="https://relay.test/v1")
        with self.assertRaisesRegex(ImagePayloadError,"without a completed image"):
            client.generate_stream(model="gpt-image-2.5-flare",prompt="x",size="1024x1024",quality="high",partial_images=0,on_partial=lambda _index,_data:None)

    @patch("scripts.image_client.OpenAI")
    def test_edit_stream_passes_reference_and_collects_final_image(self,openai_cls):
        final=png_bytes()
        openai_cls.return_value.images.edit.return_value=iter([
            {"type":"image_edit.completed","b64_json":base64.b64encode(final).decode()},
        ])
        client=ImageClient(api_key="key",base_url="https://relay.test/v1")
        with tempfile.NamedTemporaryFile(delete=False) as image:
            image.write(b"reference")
            image_path=image.name
        try:
            result=client.edit_stream(model="gpt-image-2.5-sunburst",prompt="x",size="1024x1024",quality="high",image_paths=[image_path],partial_images=0,on_partial=lambda _index,_data:None)
        finally:
            os.unlink(image_path)
        self.assertEqual(result,[final])
        call=openai_cls.return_value.images.edit.call_args.kwargs
        self.assertEqual(call["model"],"gpt-image-2.5-sunburst")
        self.assertEqual(call["stream"],True)
        self.assertEqual(call["partial_images"],0)
        self.assertTrue(call["image"].closed)
if __name__=="__main__": unittest.main()
