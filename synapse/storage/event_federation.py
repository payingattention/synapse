# -*- coding: utf-8 -*-
# Copyright 2014, 2015 OpenMarket Ltd
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

from twisted.internet import defer

from ._base import SQLBaseStore, cached
from syutil.base64util import encode_base64

import logging
from Queue import PriorityQueue, Empty


logger = logging.getLogger(__name__)


class EventFederationStore(SQLBaseStore):
    """ Responsible for storing and serving up the various graphs associated
    with an event. Including the main event graph and the auth chains for an
    event.

    Also has methods for getting the front (latest) and back (oldest) edges
    of the event graphs. These are used to generate the parents for new events
    and backfilling from another server respectively.
    """

    def get_auth_chain(self, event_ids):
        return self.get_auth_chain_ids(event_ids).addCallback(self._get_events)

    def get_auth_chain_ids(self, event_ids):
        return self.runInteraction(
            "get_auth_chain_ids",
            self._get_auth_chain_ids_txn,
            event_ids
        )

    def _get_auth_chain_ids_txn(self, txn, event_ids):
        results = set()

        base_sql = (
            "SELECT auth_id FROM event_auth WHERE event_id = ?"
        )

        front = set(event_ids)
        while front:
            new_front = set()
            for f in front:
                txn.execute(base_sql, (f,))
                new_front.update([r[0] for r in txn.fetchall()])

            new_front -= results

            front = new_front
            results.update(front)

        return list(results)

    def get_oldest_events_in_room(self, room_id):
        return self.runInteraction(
            "get_oldest_events_in_room",
            self._get_oldest_events_in_room_txn,
            room_id,
        )

    def get_oldest_events_with_depth_in_room(self, room_id):
        return self.runInteraction(
            "get_oldest_events_with_depth_in_room",
            self.get_oldest_events_with_depth_in_room_txn,
            room_id,
        )

    def get_oldest_events_with_depth_in_room_txn(self, txn, room_id):
        sql = (
            "SELECT b.event_id, MAX(e.depth) FROM events as e"
            " INNER JOIN event_edges as g"
            " ON g.event_id = e.event_id AND g.room_id = e.room_id"
            " INNER JOIN event_backward_extremities as b"
            " ON g.prev_event_id = b.event_id AND g.room_id = b.room_id"
            " WHERE b.room_id = ? AND g.is_state is ?"
            " GROUP BY b.event_id"
        )

        txn.execute(sql, (room_id, False,))

        return dict(txn.fetchall())

    def _get_oldest_events_in_room_txn(self, txn, room_id):
        return self._simple_select_onecol_txn(
            txn,
            table="event_backward_extremities",
            keyvalues={
                "room_id": room_id,
            },
            retcol="event_id",
        )

    def get_latest_events_in_room(self, room_id):
        return self.runInteraction(
            "get_latest_events_in_room",
            self._get_latest_events_in_room,
            room_id,
        )

    @cached()
    def get_latest_event_ids_in_room(self, room_id):
        return self._simple_select_onecol(
            table="event_forward_extremities",
            keyvalues={
                "room_id": room_id,
            },
            retcol="event_id",
            desc="get_latest_event_ids_in_room",
        )

    def _get_latest_events_in_room(self, txn, room_id):
        sql = (
            "SELECT e.event_id, e.depth FROM events as e "
            "INNER JOIN event_forward_extremities as f "
            "ON e.event_id = f.event_id "
            "AND e.room_id = f.room_id "
            "WHERE f.room_id = ?"
        )

        txn.execute(sql, (room_id, ))

        results = []
        for event_id, depth in txn.fetchall():
            hashes = self._get_event_reference_hashes_txn(txn, event_id)
            prev_hashes = {
                k: encode_base64(v) for k, v in hashes.items()
                if k == "sha256"
            }
            results.append((event_id, prev_hashes, depth))

        return results

    def _get_latest_state_in_room(self, txn, room_id, type, state_key):
        event_ids = self._simple_select_onecol_txn(
            txn,
            table="state_forward_extremities",
            keyvalues={
                "room_id": room_id,
                "type": type,
                "state_key": state_key,
            },
            retcol="event_id",
        )

        results = []
        for event_id in event_ids:
            hashes = self._get_event_reference_hashes_txn(txn, event_id)
            prev_hashes = {
                k: encode_base64(v) for k, v in hashes.items()
                if k == "sha256"
            }
            results.append((event_id, prev_hashes))

        return results

    def _get_prev_events(self, txn, event_id):
        results = self._get_prev_events_and_state(
            txn,
            event_id,
            is_state=0,
        )

        return [(e_id, h, ) for e_id, h, _ in results]

    def _get_prev_state(self, txn, event_id):
        results = self._get_prev_events_and_state(
            txn,
            event_id,
            is_state=True,
        )

        return [(e_id, h, ) for e_id, h, _ in results]

    def _get_prev_events_and_state(self, txn, event_id, is_state=None):
        keyvalues = {
            "event_id": event_id,
        }

        if is_state is not None:
            keyvalues["is_state"] = bool(is_state)

        res = self._simple_select_list_txn(
            txn,
            table="event_edges",
            keyvalues=keyvalues,
            retcols=["prev_event_id", "is_state"],
        )

        hashes = self._get_prev_event_hashes_txn(txn, event_id)

        results = []
        for d in res:
            edge_hash = self._get_event_reference_hashes_txn(txn, d["prev_event_id"])
            edge_hash.update(hashes.get(d["prev_event_id"], {}))
            prev_hashes = {
                k: encode_base64(v)
                for k, v in edge_hash.items()
                if k == "sha256"
            }
            results.append((d["prev_event_id"], prev_hashes, d["is_state"]))

        return results

    def _get_auth_events(self, txn, event_id):
        auth_ids = self._simple_select_onecol_txn(
            txn,
            table="event_auth",
            keyvalues={
                "event_id": event_id,
            },
            retcol="auth_id",
        )

        results = []
        for auth_id in auth_ids:
            hashes = self._get_event_reference_hashes_txn(txn, auth_id)
            prev_hashes = {
                k: encode_base64(v) for k, v in hashes.items()
                if k == "sha256"
            }
            results.append((auth_id, prev_hashes))

        return results

    def get_min_depth(self, room_id):
        """ For hte given room, get the minimum depth we have seen for it.
        """
        return self.runInteraction(
            "get_min_depth",
            self._get_min_depth_interaction,
            room_id,
        )

    def _get_min_depth_interaction(self, txn, room_id):
        min_depth = self._simple_select_one_onecol_txn(
            txn,
            table="room_depth",
            keyvalues={"room_id": room_id},
            retcol="min_depth",
            allow_none=True,
        )

        return int(min_depth) if min_depth is not None else None

    def _update_min_depth_for_room_txn(self, txn, room_id, depth):
        min_depth = self._get_min_depth_interaction(txn, room_id)

        do_insert = depth < min_depth if min_depth else True

        if do_insert:
            self._simple_upsert_txn(
                txn,
                table="room_depth",
                keyvalues={
                    "room_id": room_id,
                },
                values={
                    "min_depth": depth,
                },
            )

    def _handle_prev_events(self, txn, outlier, event_id, prev_events,
                            room_id):
        """
        For the given event, update the event edges table and forward and
        backward extremities tables.
        """
        self._simple_insert_many_txn(
            txn,
            table="event_edges",
            values=[
                {
                    "event_id": event_id,
                    "prev_event_id": e_id,
                    "room_id": room_id,
                    "is_state": False,
                }
                for e_id, _ in prev_events
            ],
        )

        # Update the extremities table if this is not an outlier.
        if not outlier:
            for e_id, _ in prev_events:
                # TODO (erikj): This could be done as a bulk insert
                self._simple_delete_txn(
                    txn,
                    table="event_forward_extremities",
                    keyvalues={
                        "event_id": e_id,
                        "room_id": room_id,
                    }
                )

            # We only insert as a forward extremity the new event if there are
            # no other events that reference it as a prev event
            query = (
                "SELECT 1 FROM event_edges WHERE prev_event_id = ?"
            )

            txn.execute(query, (event_id,))

            if not txn.fetchone():
                query = (
                    "INSERT INTO event_forward_extremities"
                    " (event_id, room_id)"
                    " VALUES (?, ?)"
                )

                txn.execute(query, (event_id, room_id))

            query = (
                "INSERT INTO event_backward_extremities (event_id, room_id)"
                " SELECT ?, ? WHERE NOT EXISTS ("
                " SELECT 1 FROM event_backward_extremities"
                " WHERE event_id = ? AND room_id = ?"
                " )"
                " AND NOT EXISTS ("
                " SELECT 1 FROM events WHERE event_id = ? AND room_id = ? "
                " AND outlier = ?"
                " )"
            )

            txn.executemany(query, [
                (e_id, room_id, e_id, room_id, e_id, room_id, False)
                for e_id, _ in prev_events
            ])

            query = (
                "DELETE FROM event_backward_extremities"
                " WHERE event_id = ? AND room_id = ?"
            )
            txn.execute(query, (event_id, room_id))

            txn.call_after(
                self.get_latest_event_ids_in_room.invalidate, room_id
            )

    def get_backfill_events(self, room_id, event_list, limit):
        """Get a list of Events for a given topic that occurred before (and
        including) the events in event_list. Return a list of max size `limit`

        Args:
            txn
            room_id (str)
            event_list (list)
            limit (int)
        """
        return self.runInteraction(
            "get_backfill_events",
            self._get_backfill_events, room_id, event_list, limit
        ).addCallback(
            self._get_events
        ).addCallback(
            lambda l: sorted(l, key=lambda e: -e.depth)
        )

    def _get_backfill_events(self, txn, room_id, event_list, limit):
        logger.debug(
            "_get_backfill_events: %s, %s, %s",
            room_id, repr(event_list), limit
        )

        event_results = set()

        # We want to make sure that we do a breadth-first, "depth" ordered
        # search.

        query = (
            "SELECT depth, prev_event_id FROM event_edges"
            " INNER JOIN events"
            " ON prev_event_id = events.event_id"
            " AND event_edges.room_id = events.room_id"
            " WHERE event_edges.room_id = ? AND event_edges.event_id = ?"
            " AND event_edges.is_state = ?"
            " LIMIT ?"
        )

        queue = PriorityQueue()

        for event_id in event_list:
            depth = self._simple_select_one_onecol_txn(
                txn,
                table="events",
                keyvalues={
                    "event_id": event_id,
                },
                retcol="depth"
            )

            queue.put((-depth, event_id))

        while not queue.empty() and len(event_results) < limit:
            try:
                _, event_id = queue.get_nowait()
            except Empty:
                break

            if event_id in event_results:
                continue

            event_results.add(event_id)

            txn.execute(
                query,
                (room_id, event_id, False, limit - len(event_results))
            )

            for row in txn.fetchall():
                if row[1] not in event_results:
                    queue.put((-row[0], row[1]))

        return event_results

    @defer.inlineCallbacks
    def get_missing_events(self, room_id, earliest_events, latest_events,
                           limit, min_depth):
        ids = yield self.runInteraction(
            "get_missing_events",
            self._get_missing_events,
            room_id, earliest_events, latest_events, limit, min_depth
        )

        events = yield self._get_events(ids)

        events = sorted(
            [ev for ev in events if ev.depth >= min_depth],
            key=lambda e: e.depth,
        )

        defer.returnValue(events[:limit])

    def _get_missing_events(self, txn, room_id, earliest_events, latest_events,
                            limit, min_depth):

        earliest_events = set(earliest_events)
        front = set(latest_events) - earliest_events

        event_results = set()

        query = (
            "SELECT prev_event_id FROM event_edges "
            "WHERE room_id = ? AND event_id = ? AND is_state = ? "
            "LIMIT ?"
        )

        while front and len(event_results) < limit:
            new_front = set()
            for event_id in front:
                txn.execute(
                    query,
                    (room_id, event_id, False, limit - len(event_results))
                )

                for e_id, in txn.fetchall():
                    new_front.add(e_id)

            new_front -= earliest_events
            new_front -= event_results

            front = new_front
            event_results |= new_front

        return event_results

    def clean_room_for_join(self, room_id):
        return self.runInteraction(
            "clean_room_for_join",
            self._clean_room_for_join_txn,
            room_id,
        )

    def _clean_room_for_join_txn(self, txn, room_id):
        query = "DELETE FROM event_forward_extremities WHERE room_id = ?"

        txn.execute(query, (room_id,))
        txn.call_after(self.get_latest_event_ids_in_room.invalidate, room_id)
