# -*- coding: utf-8 -*-

# Copyright 2014 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from synapse.api.events.utils import prune_event
from synapse.federation.units import Pdu
from syutil.jsonutil import encode_canonical_json
from syutil.base64util import encode_base64, decode_base64
from syutil.crypto.jsonsign import sign_json
from synapse.api.errors import SynapseError, Codes

import hashlib
import logging

logger = logging.getLogger(__name__)


def check_event_content_hash(event, hash_algorithm=hashlib.sha256):
    """Check whether the hash for this PDU matches the contents"""
    computed_hash = _compute_content_hash(event, hash_algorithm)
    logging.debug("Expecting hash: %s", encode_base64(computed_hash.digest()))
    if computed_hash.name not in event.hashes:
        raise SynapseError(
            400,
            "Algorithm %s not in hashes %s" % (
                computed_hash.name, list(event.hashes),
            ),
            Codes.UNAUTHORIZED,
        )
    message_hash_base64 = event.hashes[computed_hash.name]
    try:
        message_hash_bytes = decode_base64(message_hash_base64)
    except:
        raise SynapseError(
            400,
            "Invalid base64: %s" % (message_hash_base64,),
            Codes.UNAUTHORIZED,
        )
    return message_hash_bytes == computed_hash.digest()


def _compute_content_hash(event, hash_algorithm):
    event_json = event.get_full_dict()
    # TODO: We need to sign the JSON that is going out via fedaration.
    event_json.pop("age_ts", None)
    event_json.pop("unsigned", None)
    event_json.pop("signatures", None)
    event_json.pop("hashes", None)
    event_json.pop("outlier", None)
    event_json.pop("destinations", None)
    event_json_bytes = encode_canonical_json(event_json)
    return hash_algorithm(event_json_bytes)


def compute_event_reference_hash(event, hash_algorithm=hashlib.sha256):
    tmp_event = prune_event(event)
    event_json = tmp_event.get_dict()
    event_json.pop("signatures", None)
    event_json.pop("age_ts", None)
    event_json.pop("unsigned", None)
    event_json_bytes = encode_canonical_json(event_json)
    hashed = hash_algorithm(event_json_bytes)
    return (hashed.name, hashed.digest())


def compute_event_signature(event, signature_name, signing_key):
    tmp_event = prune_event(event)
    tmp_event.origin = event.origin
    tmp_event.origin_server_ts = event.origin_server_ts
    d = tmp_event.get_full_dict()
    kwargs = dict(event.unrecognized_keys)
    kwargs.update({k: v for k, v in d.items()})
    tmp_pdu = Pdu(**kwargs)
    redact_json = tmp_pdu.get_dict()
    redact_json.pop("signatures", None)
    redact_json.pop("age_ts", None)
    redact_json.pop("unsigned", None)
    logger.debug("Signing event: %s", redact_json)
    redact_json = sign_json(redact_json, signature_name, signing_key)
    return redact_json["signatures"]


def add_hashes_and_signatures(event, signature_name, signing_key,
                              hash_algorithm=hashlib.sha256):
    if hasattr(event, "old_state_events"):
        state_json_bytes = encode_canonical_json(
            [e.event_id for e in event.old_state_events.values()]
        )
        hashed = hash_algorithm(state_json_bytes)
        event.state_hash = {
            hashed.name: encode_base64(hashed.digest())
        }

    hashed = _compute_content_hash(event, hash_algorithm=hash_algorithm)

    if not hasattr(event, "hashes"):
        event.hashes = {}
    event.hashes[hashed.name] = encode_base64(hashed.digest())

    event.signatures = compute_event_signature(
        event,
        signature_name=signature_name,
        signing_key=signing_key,
    )
