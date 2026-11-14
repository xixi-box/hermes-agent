import type * as HermesSdk from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { createAgentHarness, dismissAllNotifications } from './create-agent-test-utils'
import { translateBots } from './i18n-test-helper'

const mocks = await vi.hoisted(async () => ({
  ...(await import('./create-agent-test-utils')).createMocks(),
  revealPane: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = (await import('./create-agent-test-utils')).sdkMock(
    await importOriginal(), mocks, () => translateBots
  ) as typeof HermesSdk

  return {
    ...sdk,
    host: {
      ...sdk.host,
      agents: async () => ({ agents: [], sources: [] }),
      profileRoutes: async () => [],
      revealPane: mocks.revealPane
    }
  }
})
vi.mock('./canonical-chat', async importOriginal => ({ ...(await importOriginal<object>()), createCanonicalChat: mocks.createCanonicalChat }))
vi.mock('./profile-ops', async importOriginal => ({ ...(await importOriginal<object>()), deleteBot: mocks.deleteBot }))
vi.mock('./data', async importOriginal => ({ ...(await importOriginal<object>()), saveBotMeta: mocks.saveBotMeta }))

createAgentHarness(mocks)

it('Configure model reopens the Bots pane and edits the creating owner after unmount', async () => {
  const base = mocks.request.getMockImplementation()!
  mocks.activeConnection.value = 'gateway-a'
  mocks.request.mockImplementation(async (method: string, params?: unknown) => {
    if (method === 'setup.runtime_check') {
      return { ok: false, error: 'No credentials.' }
    }

    if (method === 'profiles.list') {
      return { profiles: [{ name: 'default' }] }
    }

    return base(method, params)
  })
  const { BotsPane } = await import('./roster-pane')
  const { NotificationStack } = await import('@/components/notifications')
  const { notify } = await import('@/store/notifications')
  await dismissAllNotifications()
  mocks.notify.mockImplementation(notify)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const mountPane = () => render(<QueryClientProvider client={client}><BotsPane /></QueryClientProvider>)
  const pane = mountPane()
  render(<NotificationStack />)
  fireEvent.pointerDown(await screen.findByRole('button', { name: 'New bot or group chat' }), { button: 0, ctrlKey: false })
  fireEvent.click(await screen.findByRole('menuitem', { name: 'New bot' }))
  fireEvent.change(await screen.findByPlaceholderText('inbox-triage'), { target: { value: 'inbox-triage' } })
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Create bot' }))
  await waitFor(() => expect(mocks.createCanonicalChat).toHaveBeenCalledWith('inbox-triage', { kickoff: false }))
  pane.unmount()
  mocks.activeConnection.value = 'gateway-b'
  fireEvent.click(await screen.findByRole('button', { name: 'Configure model' }))
  expect(mocks.revealPane).toHaveBeenCalledWith('hermes-bots:pane')

  const reopened = mountPane()
  expect(await screen.findByRole('dialog', { name: 'Edit profile' })).toBeTruthy()
  expect(screen.getByText(/inbox-triage/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  await waitFor(() => expect(
    mocks.requestProfile.mock.calls.find(([, method]) => method === 'profiles.describe')?.[0]
  ).toMatchObject({ connectionId: 'gateway-a', profile: 'inbox-triage' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  await act(async () => reopened.unmount())
  mountPane()
  expect(screen.queryByRole('dialog', { name: 'Edit profile' })).toBeNull()
  client.clear()
})
