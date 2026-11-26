"""MCP virtual-output contract through fake client only, no engine/socket I/O."""
import asyncio
import json
import threading
import unittest

from mcp.server.mcpserver.exceptions import ToolError
from native_services.unified_controller_control import ControllerControlError, VIRTUAL_PAD_TYPES
from tools.waystone_mcp import create_mcp_app


class OutputClient:
    def __init__(self):
        self.calls = []
        self.current = self.default = 'xbox360'
        self.failure = None
        self.invalid = False
        self.block = None
        self.entered = threading.Event()
        self.profiles = {'General': {'stick_processing': {
            'left': {'enabled': True, 'inner_deadzone': 0.08, 'outer_deadzone': 1.0, 'anti_deadzone': 0.0, 'curve': 1.0},
            'right': {'enabled': True, 'inner_deadzone': 0.08, 'outer_deadzone': 1.0, 'anti_deadzone': 0.0, 'curve': 1.0}}}}
        self.revision = 1

    def request(self, op, **fields):
        self.calls.append((op, fields))
        self.entered.set()
        if self.block: self.block.wait(2)
        if op == 'output_get':
            if self.invalid: return {}
            return dict(current_type=self.current, default_type=self.default,
                        supported_types=list(VIRTUAL_PAD_TYPES), game_state='idle',
                        game_observation_age_seconds=0, game_observation_scope='waystone_tracked_games', revision='a' * 64)
        if op == 'output_set':
            if self.failure: raise self.failure
            self.current = fields['type']
            if fields['persist_default']: self.default = self.current
            return {}
        if op == 'profiles':
            return {'active_profile': 'General', 'names': list(self.profiles), 'revision': f'{self.revision:064x}'}
        if op == 'profile_get':
            return {'name': fields['name'], 'profile': json.loads(json.dumps(self.profiles[fields['name']])),
                    'revision': f'{self.revision:064x}'}
        if op == 'profile_update':
            if fields['expected_revision'] != f'{self.revision:064x}':
                raise ControllerControlError('ERR_REVISION_CONFLICT')
            self.profiles[fields['name']].update(json.loads(json.dumps(fields['profile'])))
            self.revision += 1
            return {'name': fields['name'], 'revision': f'{self.revision:064x}'}
        raise AssertionError(op)


class TestMcpVirtualPad(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = OutputClient()
        self.app = create_mcp_app(None, controller_client=self.client)

    async def test_getter_reads_actual_default_and_scope_without_mutating(self):
        result = await self.app.call_tool('waystone_get_virtual_gamepad_profile', {})
        data = json.loads(result.content[0].text)
        self.assertEqual(data['game_observation_scope'], 'waystone_tracked_games')
        self.assertEqual(data['current_type'], 'xbox360')
        self.assertEqual(self.client.calls, [('output_get', {})])

    async def test_existing_setter_name_driver_type_alias_applies_and_persists_natively(self):
        result = await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': ' XBOXSeries '})
        data = json.loads(result.content[0].text)
        self.assertEqual(data['virtual_gamepad_driver'], 'xboxseries')
        self.assertEqual(data['status'], 'applied')
        self.assertEqual(self.client.calls, [('output_set', {'type': 'xboxseries', 'force': False, 'persist_default': True}), ('output_get', {})])

    async def test_force_only_when_explicitly_requested(self):
        await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'dualshock4', 'force': True})
        self.assertTrue(self.client.calls[0][1]['force'])

    async def test_unsupported_stretch_type_never_sent(self):
        with self.assertRaises(ToolError):
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'switchpro'})
        self.assertEqual(self.client.calls, [])

    async def test_active_rejection_reads_back_and_does_not_retry_or_write_local_intent(self):
        self.client.failure = ControllerControlError('ERR_GAME_ACTIVE')
        with self.assertRaises(ToolError) as ctx:
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'dualsense'})
        self.assertIn('actual=xbox360', str(ctx.exception))
        self.assertEqual([op for op, _ in self.client.calls], ['output_set', 'output_get'])
        self.assertEqual(self.client.default, 'xbox360')

    async def test_ambiguous_failure_never_exposes_private_exception(self):
        self.client.failure = RuntimeError('secret path /home/private')
        with self.assertRaises(ToolError) as ctx:
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'dualsense'})
        self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual([op for op, _ in self.client.calls], ['output_set', 'output_get'])

    async def test_invalid_readback_is_not_claimed_as_applied(self):
        self.client.invalid = True
        with self.assertRaises(ToolError) as ctx:
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'xboxone'})
        self.assertIn('unverified', str(ctx.exception))
        self.assertEqual([op for op, _ in self.client.calls], ['output_set', 'output_get'])

    async def test_native_io_does_not_block_async_mcp_transport(self):
        self.client.block = threading.Event()
        task = asyncio.create_task(self.app.call_tool('waystone_get_virtual_gamepad_profile', {}))
        for _ in range(100):
            if self.client.entered.is_set(): break
            await asyncio.sleep(0.002)
        self.assertTrue(self.client.entered.is_set())
        self.assertFalse(task.done())
        self.client.block.set()
        await task


class TestMcpStickDeadzones(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = OutputClient()
        self.app = create_mcp_app(None, controller_client=self.client)

    async def test_sets_profile_stick_processing_through_engine_with_revision(self):
        result = await self.app.call_tool('waystone_set_controller_deadband',
                                          {'deadband': 0.12, 'stick': 'left', 'anti_deadzone': 0.05})
        data = json.loads(result.content[0].text)
        left = self.client.profiles['General']['stick_processing']['left']
        right = self.client.profiles['General']['stick_processing']['right']
        self.assertEqual((left['inner_deadzone'], left['anti_deadzone'], left['curve']), (0.12, 0.05, 1.0))
        self.assertEqual(right['inner_deadzone'], 0.08)
        self.assertEqual(data['status'], 'applied')
        update = [f for op, f in self.client.calls if op == 'profile_update'][0]
        self.assertEqual(update['expected_revision'], f'{1:064x}')

    async def test_rejects_device_ids_and_bad_values_without_engine_calls(self):
        for args in ({'deadband': 0.1, 'device_id': 'pad'}, {'deadband': 0.9}, {'deadband': 0.1, 'stick': 'middle'}):
            with self.assertRaises(ToolError):
                await self.app.call_tool('waystone_set_controller_deadband', args)
        self.assertEqual(self.client.calls, [])


class TestMcpProfilePadType(unittest.IsolatedAsyncioTestCase):
    """Pad type lives in a controller profile (Kian 2026-11-25)."""

    def setUp(self):
        self.client = OutputClient()
        self.app = create_mcp_app(None, controller_client=self.client)

    async def test_profile_pad_type_saved_with_revision_and_read_back(self):
        result = await self.app.call_tool('waystone_set_virtual_gamepad_profile',
                                          {'driver_type': 'dualsense', 'profile': 'General'})
        data = json.loads(result.content[0].text)
        self.assertEqual(self.client.profiles['General']['virtual_gamepad_driver'], 'dualsense')
        self.assertEqual((data['profile'], data['virtual_gamepad_driver'], data['status']), ('General', 'dualsense', 'saved'))
        self.assertFalse(any(op == 'output_set' for op, _ in self.client.calls))
        await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'default', 'profile': 'General'})
        self.assertIsNone(self.client.profiles['General']['virtual_gamepad_driver'])

    async def test_bad_type_or_missing_profile_changes_nothing(self):
        with self.assertRaises(ToolError):
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'n64', 'profile': 'General'})
        with self.assertRaises(ToolError):
            await self.app.call_tool('waystone_set_virtual_gamepad_profile', {'driver_type': 'ds4', 'profile': 'Nope'})
        self.assertFalse(any(op == 'profile_update' for op, _ in self.client.calls))
