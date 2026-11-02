// Asks the user's device (Touch ID / Face ID / security key) to sign an approval statement.
import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { api, type ApprovalOptions } from './api'

/** Returns the passkey assertion to send with an approval, or undefined when no passkey is set
 *  up and none is required. Throws if the user cancels. */
export async function signApproval(getOptions: () => Promise<ApprovalOptions>): Promise<unknown> {
  const opts = await getOptions()
  if (!opts.passkey) {
    if (opts.required) throw new Error('A passkey is required. Set one up on the Security page.')
    return undefined
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return startAuthentication({ optionsJSON: opts.options as any })
}

export async function enrollPasskey(label: string) {
  const options = await api.passkeyRegistrationOptions()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const credential = await startRegistration({ optionsJSON: options as any })
  return api.registerPasskey(credential, label)
}

/** Passwordless sign-in: the device signs a one-time sign-in statement. */
export async function signInWithPasskey() {
  const opts = await api.passkeySignInOptions()
  if (!opts.passkey) throw new Error('No passkey is set up yet: sign in with your password, then add one on the Security page.')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const credential = await startAuthentication({ optionsJSON: opts.options as any })
  return api.passkeySignIn(opts.nonce, credential)
}

export function describeError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  if (/NotAllowedError|timed out|not allowed/i.test(msg)) return 'Passkey request was cancelled or timed out.'
  return msg
}
