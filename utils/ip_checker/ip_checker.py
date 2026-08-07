import gzip
import hashlib
import os
import shutil
import socket
from urllib.parse import urlparse

import ipdb

from utils.tools import resource_path


DATABASE_PATH = "utils/ip_checker/data/qqwry.ipdb"
COMPRESSED_DATABASE_PATH = f"{DATABASE_PATH}.gz"
DATABASE_HASH_SUFFIX = ".sha256"


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_database() -> str:
    compressed_path = resource_path(COMPRESSED_DATABASE_PATH)
    if not os.path.isfile(compressed_path):
        database_path = resource_path(DATABASE_PATH)
        if os.path.isfile(database_path):
            return database_path
        raise FileNotFoundError(
            f"IP database was not found at {database_path} or {compressed_path}"
        )

    database_path = resource_path(DATABASE_PATH, persistent=True)
    hash_path = f"{database_path}{DATABASE_HASH_SUFFIX}"
    compressed_hash = _file_sha256(compressed_path)
    if os.path.isfile(database_path) and os.path.isfile(hash_path):
        with open(hash_path, encoding="ascii") as file:
            if file.read().strip() == compressed_hash:
                return database_path

    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    temporary_path = f"{database_path}.{os.getpid()}.tmp"
    temporary_hash_path = f"{hash_path}.{os.getpid()}.tmp"
    try:
        with gzip.open(compressed_path, "rb") as source:
            with open(temporary_path, "wb") as destination:
                shutil.copyfileobj(source, destination)
        os.replace(temporary_path, database_path)
        with open(temporary_hash_path, "w", encoding="ascii") as file:
            file.write(compressed_hash)
        os.replace(temporary_hash_path, hash_path)
    finally:
        for path in (temporary_path, temporary_hash_path):
            if os.path.exists(path):
                os.remove(path)
    return database_path


class IPChecker:
    def __init__(self):
        self.db = ipdb.City(prepare_database())
        self.url_host = {}
        self.host_ip = {}
        self.host_ipv_type = {}

    def get_host(self, url: str) -> str:
        """
        Get the host from a URL
        """
        if url in self.url_host:
            return self.url_host[url]

        host = urlparse(url).hostname or url
        self.url_host[url] = host
        return host

    def get_ip(self, url: str) -> str | None:
        """
        Get the IP from a URL
        """
        host = self.get_host(url)
        if host in self.host_ip:
            return self.host_ip[host]

        self.get_ipv_type(url)
        return self.host_ip.get(host)

    def get_ipv_type(self, url: str) -> str:
        """
        Get the IPv type of URL
        """
        host = self.get_host(url)
        if host in self.host_ipv_type:
            return self.host_ipv_type[host]

        try:
            addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            ip = next((info[4][0] for info in addr_info if info[0] == socket.AF_INET6), None)
            if not ip:
                ip = next((info[4][0] for info in addr_info if info[0] == socket.AF_INET), None)
            ipv_type = "ipv6" if any(info[0] == socket.AF_INET6 for info in addr_info) else "ipv4"
        except Exception:
            ip = None
            ipv_type = "ipv4"

        self.host_ip[host] = ip
        self.host_ipv_type[host] = ipv_type
        return ipv_type

    def find_map(self, ip: str) -> tuple[str | None, str | None]:
        """
        Find the IP address and return the location and ISP
        :param ip: The IP address to find
        :return: A tuple of (location, ISP)
        """
        try:
            result = self.db.find_map(ip, "CN")
            if not result:
                return None, None

            location_parts = [
                result.get('country_name', ''),
                result.get('region_name', ''),
                result.get('city_name', '')
            ]
            location = "-".join(filter(None, location_parts))
            isp = result.get('isp_domain', None)

            return location, isp
        except Exception as e:
            print(f"Error on finding ip location and ISP: {e}")
            return None, None
