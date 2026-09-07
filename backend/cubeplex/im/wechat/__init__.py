"""WeChat (微信公众号) IM platform — registers itself with the platform registry."""

from cubeplex.im.registry import register_platform
from cubeplex.im.wechat._platform import WeChatPlatform

register_platform("wechat", WeChatPlatform())
