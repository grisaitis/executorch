# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict
import unittest
from typing import List, Optional, Tuple, Union

import executorch.sdk.etdump.schema_flatcc as flatcc
from executorch.sdk.etdump.schema_flatcc import ETDumpFlatCC, ProfileEvent
from executorch.sdk.inspector import Event, EventBlock, PerfData
from executorch.sdk.inspector._inspector import (
    DelegateMetadata,
    EventSignature,
    InstructionEvent,
    InstructionEventSignature,
    ProfileEventSignature,
)


class TestEventBlock(unittest.TestCase):

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Test Helpers ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @staticmethod
    def _gen_sample_profile_event(
        name: str,
        instruction_id: int,
        time: Tuple[int, int],
        delegate_debug_id: Optional[Union[int, str]] = None,
        delegate_debug_metadata: Optional[str] = None,
    ) -> flatcc.ProfileEvent:
        """
        Helper for generating test ProfileEvents

        Notably:
        - the timestamp is specified as a tuple of two separate integers
        - delegate_debug_id takes either the str or int representation
        - chain_idx is auto-populated to 0
        """
        delegate_debug_id_int = (
            delegate_debug_id if isinstance(delegate_debug_id, int) else -1
        )
        delegate_debug_id_str = (
            delegate_debug_id if isinstance(delegate_debug_id, str) else ""
        )
        return flatcc.ProfileEvent(
            name,
            0,
            instruction_id,
            delegate_debug_id_int,
            delegate_debug_id_str,
            # pyre-fixme[6]: For 6th argument expected `Optional[bytes]` but got
            #  `Optional[str]`.
            delegate_debug_metadata,
            start_time=time[0],
            end_time=time[1],
        )

    @staticmethod
    def _get_sample_etdump_flatcc() -> flatcc.ETDumpFlatCC:
        """
        Helper for getting a sample ETDumpFlatCC object with 3 RunData:
        - run_data_1 has signature_a with just profile_1
        - run_data_2 has the same signature with run_data_1, but different times
        - run_data_3 has signature_b with both (profile_1, profile_2)
        """
        profile_event_1 = TestEventBlock._gen_sample_profile_event(
            name="profile_1", instruction_id=1, time=(0, 1), delegate_debug_id=100
        )
        run_data_1 = flatcc.RunData(
            name="signature_a",
            bundled_input_index=-1,
            allocators=[],
            events=[
                flatcc.Event(
                    allocation_event=None,
                    debug_event=None,
                    profile_event=profile_event_1,
                )
            ],
        )
        profile_event_2 = TestEventBlock._gen_sample_profile_event(
            name="profile_1", instruction_id=1, time=(2, 4), delegate_debug_id=100
        )
        run_data_2 = flatcc.RunData(
            name="signature_a",
            bundled_input_index=-1,
            allocators=[],
            events=[
                flatcc.Event(
                    allocation_event=None,
                    debug_event=None,
                    profile_event=profile_event_2,
                )
            ],
        )

        profile_event_3 = TestEventBlock._gen_sample_profile_event(
            name="profile_1", instruction_id=1, time=(5, 6), delegate_debug_id=100
        )
        profile_event_4 = TestEventBlock._gen_sample_profile_event(
            name="profile_2", instruction_id=2, time=(7, 8), delegate_debug_id=100
        )
        run_data_3 = flatcc.RunData(
            name="signature_b",
            bundled_input_index=-1,
            allocators=[],
            events=[
                flatcc.Event(
                    allocation_event=None,
                    debug_event=None,
                    profile_event=profile_event_3,
                ),
                flatcc.Event(
                    allocation_event=None,
                    debug_event=None,
                    profile_event=profile_event_4,
                ),
            ],
        )

        return ETDumpFlatCC(version=0, run_data=[run_data_1, run_data_2, run_data_3])

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Tests ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def test_gen_from_etdump(self) -> None:
        """
        Test "e2e" generation of EventBlocks given an ETDump
            - Generated via EventBock.gen_from_etdump

        Specifically it tests for external correctness:
        - Correct number of EventBlocks
        - Correct number of Events and Raw Data values (iterations)
        """
        etdump: ETDumpFlatCC = TestEventBlock._get_sample_etdump_flatcc()
        blocks: List[EventBlock] = EventBlock._gen_from_etdump(etdump)

        self.assertEqual(len(blocks), 2, f"Expected 2 runs, got {len(blocks)}")

        # One EventBlock should have 1 event with 2 iterations
        # The other EventBlock should have 2 events with 1 iterations
        run_counts = set()
        for block in blocks:
            if (perf_data := block.events[0].perf_data) is not None:
                run_counts.add((len(block.events), len(perf_data.raw)))
        self.assertSetEqual(run_counts, {(1, 2), (2, 1)})

    def test_inspector_event_generation(self) -> None:
        """
        Test Inspector.Event derivation from various ProfileEvent cases
        - Non Delegated
        - Delegate with Int Debug ID
        - Delegate with String Debug ID
        """

        def _test_profile_event_generation(
            name: str,
            instruction_id: int,
            delegate_debug_id_int: Optional[int] = None,
            delegate_debug_id_str: Optional[str] = None,
            scale_factor: int = 1000,
        ) -> None:
            """
            Helper function for testing that the provided ProfileEvent fields are
            properly translated to Inspector.ProfileEventSignature and Inspector.Event
            """
            delegate_debug_id = delegate_debug_id_int or delegate_debug_id_str
            profile_event: flatcc.ProfileEvent = (
                TestEventBlock._gen_sample_profile_event(
                    name,
                    instruction_id,
                    (0, 1),
                    delegate_debug_id,
                )
            )

            # Test Signature Generation
            profile_signature = ProfileEventSignature._gen_from_event(profile_event)
            expected_signature = ProfileEventSignature(
                name,
                instruction_id,
                delegate_debug_id_int,
                delegate_debug_id_str,
            )
            self.assertEqual(profile_signature, expected_signature)

            event_signature = EventSignature(
                instruction_id=instruction_id, profile_event_signature=profile_signature
            )

            # Test Event Generation
            durations = [10, 20, 30]
            delegate_debug_metadatas = ["metadata_0", "metadata_1", "metadata_2"]
            profile_events: List[flatcc.ProfileEvent] = [
                TestEventBlock._gen_sample_profile_event(
                    name,
                    instruction_id,
                    (0, time),
                    delegate_debug_id,
                    (
                        delegate_debug_metadatas[index]
                        if delegate_debug_id is not None
                        else None
                    ),
                )
                for index, time in enumerate(durations)
            ]
            instruction_events = [
                InstructionEvent(
                    signature=InstructionEventSignature(
                        instruction_id=instruction_id, chain_index=0
                    ),
                    profile_events=[profile_event],
                )
                for profile_event in profile_events
            ]
            event = Event._gen_from_inference_events(
                event_signature, instruction_events, scale_factor=scale_factor
            )

            is_delegated = delegate_debug_id is not None
            expected_event = Event(
                name=str(delegate_debug_id) if is_delegated else name,
                perf_data=PerfData(
                    [float(duration) / scale_factor for duration in durations]
                ),
                delegate_debug_identifier=delegate_debug_id,
                is_delegated_op=is_delegated,
                _delegate_debug_metadatas=(
                    delegate_debug_metadatas if is_delegated else []
                ),
                _instruction_id=event_signature.instruction_id,
            )
            self.assertEqual(event, expected_event)

            # Test delegate_debug_metadata_parsing
            if is_delegated:
                expected_event = Event(
                    name=str(delegate_debug_id) if is_delegated else name,
                    perf_data=PerfData(
                        [float(duration) / scale_factor for duration in durations]
                    ),
                    delegate_debug_identifier=delegate_debug_id,
                    is_delegated_op=is_delegated,
                    _delegate_debug_metadatas=delegate_debug_metadatas,
                    _instruction_id=event_signature.instruction_id,
                    _delegate_metadata_parser=lambda metadatas: {
                        "joined": "-".join(metadatas)
                    },
                )
                self.assertEqual(
                    expected_event.delegate_debug_metadatas,
                    {"joined": "-".join(delegate_debug_metadatas)},
                )

        # Non Delegated
        _test_profile_event_generation("non-delegate", 1)

        # Delegate with Int Debug ID
        _test_profile_event_generation("delegate", 1, 100)

        # Delegate with String Debug ID
        _test_profile_event_generation("delegate", 1, None, "identifier")

        # Manipulating the scale factor
        _test_profile_event_generation(
            "delegate", 1, None, "identifier", scale_factor=10000
        )

    def test_gen_resolve_debug_handles(self) -> None:
        """
        Test that gen_resolve_debug_handles() correctly populates the EventBlock
        """

        def _gen_event_helper(events: List[ProfileEvent]) -> Event:
            """
            Helper function to generate an Event given a set of ProfileEvents
            """
            profile_event = events[0]
            profile_signature = ProfileEventSignature._gen_from_event(profile_event)
            event_signature = EventSignature(
                profile_event.instruction_id, profile_signature
            )
            instruction_events = [
                InstructionEvent(
                    signature=InstructionEventSignature(
                        instruction_id=profile_event.instruction_id,
                        chain_index=profile_event.chain_index,
                    ),
                    profile_events=[event],
                )
                for event in events
            ]
            return Event._gen_from_inference_events(event_signature, instruction_events)

        # Create Test Data

        # Non-Delegated
        non_delegated_profile_events_1 = [
            TestEventBlock._gen_sample_profile_event("non_del_1", 0, (0, 1)),
            TestEventBlock._gen_sample_profile_event("non_del_1", 0, (0, 1)),
        ]
        non_delegated_profile_events_2 = [
            TestEventBlock._gen_sample_profile_event("non_del_2", 1, (0, 1)),
        ]
        non_delegated_event_1 = _gen_event_helper(non_delegated_profile_events_1)
        non_delegated_event_2 = _gen_event_helper(non_delegated_profile_events_2)

        # Delegated
        delegated_profile_events_1 = [
            TestEventBlock._gen_sample_profile_event("del_1", 0, (0, 1), 10),
            TestEventBlock._gen_sample_profile_event("del_1", 0, (0, 1), 10),
            TestEventBlock._gen_sample_profile_event("del_1", 0, (0, 1), 10),
        ]
        delegated_profile_events_2 = [
            TestEventBlock._gen_sample_profile_event("del_2", 2, (0, 1), 20),
        ]
        delegated_event_1 = _gen_event_helper(delegated_profile_events_1)
        delegated_event_2 = _gen_event_helper(delegated_profile_events_2)

        # Create Test EventBlock
        event_block = EventBlock(
            name="test_name_1",
            events=[
                non_delegated_event_1,
                non_delegated_event_2,
                delegated_event_1,
                delegated_event_2,
            ],
        )

        # Create Test Maps
        handle_map = {"0": [100], "1": [110], "2": [120]}
        delegate_map = {
            "0": DelegateMetadata(
                {
                    "name": "delegate",
                    "delegate_map": {10: (100, 1000)},
                }
            ),
            "2": DelegateMetadata(
                {
                    "name": "delegate_2",
                    "delegate_map": {20: (200,)},
                }
            ),
        }
        event_block._gen_resolve_debug_handles(handle_map, delegate_map)

        # Verify Results
        for event in event_block.events:
            # To satisfy type checker
            assert event._instruction_id is not None
            if (
                delegate_debug_identifier := event.delegate_debug_identifier
            ) is not None:
                # Delegated
                metadata = delegate_map[str(event._instruction_id)]
                self.assertEqual(event.delegate_backend_name, metadata["name"])
                self.assertEqual(
                    event.debug_handles,
                    metadata["delegate_map"][delegate_debug_identifier],  # pyre-ignore
                )
            else:
                # Non Delegated
                self.assertEqual(
                    event.debug_handles, handle_map[str(event._instruction_id)]
                )
