"""Proof tests validating that the test network blocker strictly intercepts all network calls."""

import socket

import httpx
import pytest


def test_network_blocker_intercepts_httpx_loopback():
    with pytest.raises(RuntimeError, match=r"Network access blocked in tests: httpx request to http://127.0.0.1:9"):
        with httpx.Client() as client:
            client.get("http://127.0.0.1:9")


def test_network_blocker_intercepts_httpx_public_host():
    with pytest.raises(RuntimeError, match=r"Network access blocked in tests: httpx request to http://example.com"):
        with httpx.Client() as client:
            client.get("http://example.com")


def test_network_blocker_intercepts_dns_lookup():
    with pytest.raises(RuntimeError, match=r"Network access blocked in tests: DNS lookup for example.com"):
        socket.getaddrinfo("example.com", 80)


def test_network_blocker_intercepts_raw_socket_connect_non_loopback():
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match=r"Network access blocked in tests: raw socket connect to 8.8.8.8"):
            s.connect(("8.8.8.8", 53))
    finally:
        s.close()
