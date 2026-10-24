"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import copy
import httpx2
from anthropic import APIConnectionError, APIStatusError
from service_fixtures import ModelService


class RetryModel(ModelService):
    async def create(self, **parameters):
        self.requests.append(copy.deepcopy(parameters))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def connection_error():
    return APIConnectionError(request=httpx2.Request("POST", "https://model.example.test"))


def status_error(status, body=None, headers=None):
    return APIStatusError("provider failed", response=httpx2.Response(status,
        request=httpx2.Request("POST", "https://model.example.test"), headers=headers), body=body)


def call(identity, name, arguments):
    return {"type": "tool_use", "id": identity, "name": name, "input": arguments}


class FailingAfterTools(ModelService):
    async def create(self, **kwargs):
        if len(self.requests) == 1:
            self.requests.append(kwargs)
            raise APIStatusError("账户额度耗尽", response=httpx2.Response(402,
                request=httpx2.Request("POST", "https://model.example.test")), body={"error": {"type": "quota_error"}})
        return await super().create(**kwargs)


__all__ = ['RetryModel', 'connection_error', 'status_error', 'call', 'FailingAfterTools']
