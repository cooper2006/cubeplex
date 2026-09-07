import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'
import { StepWechatQR } from '../steps/StepWechatQR'

import type { PlatformDescriptor } from './types'

export interface WeChatConnectResult {
  code: string
  qrcodeUrl: string | null
  instruction: string
  expiresIn: number
}

export const wechatDescriptor: PlatformDescriptor = {
  id: 'wechat',
  labelKey: 'im.platform.wechat.label',
  iconName: 'MessageSquare',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.wechat.prereq.app',
      helpUrl: () => 'https://ilinkai.weixin.qq.com/',
    },
    {
      key: 'bot',
      labelKey: 'im.wizard.wechat.prereq.bot',
      helpUrl: () => 'https://ilinkai.weixin.qq.com/',
    },
  ],
  credentialFields: [
    {
      key: 'bot_token',
      labelKey: 'im.wizard.wechat.field.botToken',
      type: 'password',
      required: false,
      placeholder: '输入 Bot Token（可选）',
    },
  ],
  steps: [
    {
      key: 'prereqs',
      labelKey: 'im.wizard.step.prereqs',
      Component: StepPrereqs,
      canAdvance: () => true,
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.step.credentials',
      Component: StepCredentials,
      canAdvance: () => true,
    },
    {
      key: 'qrcode',
      labelKey: 'im.wizard.wechat.step.qrcode',
      Component: StepWechatQR,
      canAdvance: () => true,
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'wechat' as const,
    bot_token: f.bot_token || '',
    qrcode_login: f.qrcode_login === 'true',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://ilinkai.weixin.qq.com/',
}
