import unittest
from unittest.mock import patch

from autodl_api import AutoDLClient, AutoDLWebClient


class ApiPayloadTests(unittest.TestCase):
    def test_missing_token_is_rejected_locally(self):
        result = AutoDLClient("").list_instances()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NO_TOKEN")

    def test_create_payload(self):
        client = AutoDLClient("test")
        with patch.object(client, "request") as request:
            client.create_instance("4090D", "image-1", 1, 118, "watch", [], "")
            method, path, payload = request.call_args.args
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v1/dev/instance/pro/create")
            self.assertNotIn("data_center_list", payload)
            self.assertEqual(payload["gpu_spec_uuid"], "4090D")

    def test_image_list_endpoint(self):
        client = AutoDLClient("test")
        with patch.object(client, "request") as request:
            client.list_images()
            method, path, payload = request.call_args.args
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v1/dev/instance/pro/image/private/list")
            self.assertEqual(payload["page_size"], 100)

    def test_web_instance_list_endpoint(self):
        client = AutoDLWebClient("session")
        with patch.object(client, "request") as request:
            request.return_value.ok = True
            request.return_value.data = {"list": []}
            client.list_instances()
            method, path, payload = request.call_args.args
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v1/instance")


if __name__ == "__main__":
    unittest.main()
