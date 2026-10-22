from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


API_ROOT = "https://api.autodl.com"


@dataclass
class ApiResult:
    ok: bool
    data: Any = None
    message: str = ""
    code: str = ""


class AutoDLClient:
    def __init__(self, token: str, timeout: int = 12):
        self.token = token.strip()
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None) -> ApiResult:
        if not self.token:
            return ApiResult(False, message="尚未配置开发者 Token", code="NO_TOKEN")
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            API_ROOT + path,
            data=body,
            method=method,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "AutoDL-Watch/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            ok = parsed.get("code") == "Success"
            return ApiResult(ok, parsed.get("data"), parsed.get("msg", ""), parsed.get("code", ""))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            return ApiResult(False, message=f"HTTP {exc.code}: {detail}", code="HTTP_ERROR")
        except urllib.error.URLError as exc:
            return ApiResult(False, message=f"网络错误：{exc.reason}", code="NETWORK_ERROR")
        except Exception as exc:
            return ApiResult(False, message=f"{type(exc).__name__}: {exc}", code="CLIENT_ERROR")

    def list_instances(self) -> ApiResult:
        return self.request("POST", "/api/v1/dev/instance/pro/list", {"page_index": 1, "page_size": 100})

    def list_images(self) -> ApiResult:
        return self.request(
            "POST", "/api/v1/dev/instance/pro/image/private/list",
            {"page_index": 1, "page_size": 100},
        )

    def power_on(self, instance_uuid: str, start_command: str = "") -> ApiResult:
        payload = {"instance_uuid": instance_uuid, "payload": "gpu"}
        if start_command.strip():
            payload["start_command"] = start_command.strip()
        return self.request("POST", "/api/v1/dev/instance/pro/power_on", payload)

    def create_instance(
        self,
        gpu_spec_uuid: str,
        image_uuid: str,
        gpu_amount: int,
        cuda_v_from: int,
        instance_name: str,
        regions: list[str],
        start_command: str = "",
    ) -> ApiResult:
        payload: dict[str, Any] = {
            "req_gpu_amount": gpu_amount,
            "expand_system_disk_by_gb": 0,
            "gpu_spec_uuid": gpu_spec_uuid,
            "image_uuid": image_uuid,
            "cuda_v_from": cuda_v_from,
            "instance_name": instance_name or "AutoDL Watch 创建",
        }
        if regions:
            payload["data_center_list"] = regions
        if start_command.strip():
            payload["start_command"] = start_command.strip()
        return self.request("POST", "/api/v1/dev/instance/pro/create", payload)

    def region_stock(self, region_sign: str, gpu_name: str = "") -> ApiResult:
        payload: dict[str, Any] = {"region_sign": region_sign}
        if gpu_name.strip():
            payload["gpu_name_set"] = [gpu_name.strip()]
        return self.request("POST", "/api/v1/dev/machine/region/gpu_stock", payload)


class AutoDLWebClient(AutoDLClient):
    """Unofficial web-console API. Endpoints may change without notice."""

    def request(self, method: str, path: str, payload: dict | None = None) -> ApiResult:
        if not self.token:
            return ApiResult(False, message="尚未登录 AutoDL 网页", code="NO_SESSION")
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            "https://www.autodl.com/" + path.lstrip("/"),
            data=body,
            method=method,
            headers={
                "Authorization": self.token,
                "AppVersion": "0.0.0",
                "Content-Type": "application/json",
                "User-Agent": "AutoDL-Watch/0.2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                parsed = json.loads(response.read().decode("utf-8", errors="replace"))
            ok = parsed.get("code") == "Success"
            return ApiResult(ok, parsed.get("data"), parsed.get("msg", ""), parsed.get("code", ""))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            return ApiResult(False, message=f"HTTP {exc.code}: {detail}", code="HTTP_ERROR")
        except urllib.error.URLError as exc:
            return ApiResult(False, message=f"网络错误：{exc.reason}", code="NETWORK_ERROR")
        except Exception as exc:
            return ApiResult(False, message=f"{type(exc).__name__}: {exc}", code="CLIENT_ERROR")

    def list_instances(self) -> ApiResult:
        result = self.request("POST", "/api/v1/instance", {"page_index": 1, "page_size": 100})
        if result.ok and isinstance(result.data, dict) and "list" not in result.data:
            # Some console versions nest pagination one level deeper.
            nested = result.data.get("data")
            if isinstance(nested, dict):
                result.data = nested
        return result

    def power_on(self, instance_uuid: str, start_command: str = "") -> ApiResult:
        return self.request("POST", "/api/v1/instance/power_on", {"instance_uuid": instance_uuid, "payload": "gpu"})

