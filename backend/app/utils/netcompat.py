"""网络兼容补丁：本机 IPv6 链路异常时，出站 HTTP 强制优先 IPv4。

现象：部分站点（东财等）IPv6 连接被重置（RemoteDisconnected），而 IPv4 正常；
urllib3 的 IPv6→IPv4 回退在部分环境下不可靠。
做法：过滤 getaddrinfo 结果优先返回 IPv4；仅 IPv6 的域名不受影响。幂等，进程级生效。
"""
import socket

_patched = False


def patch_ipv4_preference() -> bool:
    """出站连接优先 IPv4。返回是否本次生效。"""
    global _patched
    if _patched:
        return False
    _patched = True
    original_getaddrinfo = socket.getaddrinfo

    def _prefer_ipv4(host, *args, **kwargs):
        results = original_getaddrinfo(host, *args, **kwargs)
        ipv4 = [r for r in results if r[0] == socket.AF_INET]
        return ipv4 if ipv4 else results

    socket.getaddrinfo = _prefer_ipv4
    return True
