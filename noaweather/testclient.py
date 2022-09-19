#!/usr/bin/python
"""
Example weather test client

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2022 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""
import socket
import pickle as cPickle
import sys

from pprint import pprint

# tests requests
tests = [
    "?%f|%f" % (41.38, 2.18),  # Request weather data for lat/lon
    '?LEBL',  # Request metar of the station
    '?KSEA',
    '?SKBO',
    # '!reload',     # Reload configuration
    # '!shutdown',   # Shutdown server
]

if len(sys.argv) > 1:
    tests = sys.argv[1:]

HOST, PORT = "127.0.0.1", 8950

for request in tests:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(request.encode('utf-8'), (HOST, PORT))
    received = sock.recv(1024 * 8)

    print("Request: %s \nResponse:" % (request))
    pprint(cPickle.loads(received), width=160)
