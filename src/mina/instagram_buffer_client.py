"""InstagramのBuffer通信。既存Xクライアントと同じ要求数台帳を使う。"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .buffer_limits import record_headers, record_request
from .buffer_trial import ProbeError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class InstagramBufferClient:
    def __init__(self, store, token=None, transport=None):
        self.store, self.token, self.transport = store, token or os.getenv('BUFFER_API_KEY', ''), transport
        if not self.token or '\r' in self.token or '\n' in self.token:
            raise ProbeError('buffer_key_missing_or_invalid')

    def _request(self, query, variables=None):
        from .routine_buffer import maybe_refresh
        self._token=self.token
        maybe_refresh(self,self.store,self.token)
        if query.lstrip().startswith('mutation'):
            from .publication_hold import require_buffer_mutation
            require_buffer_mutation(self.store, getattr(self, 'publication_target', None),getattr(self,'routine_publication_claim',None))
        from .buffer_budget import subject,observe
        identity=subject(self.token);request_id=record_request(self.store, query,subject=identity)
        try:
            if self.transport is not None:
                result = self.transport(query, variables or {})
            else:
                body = json.dumps({'query': query, 'variables': variables or {}}, ensure_ascii=False).encode()
                request = Request('https://api.buffer.com', data=body,
                                  headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
                with build_opener(NoRedirect()).open(request, timeout=20) as response:
                    raw = response.read(262145)
                    record_headers(self.store, response.headers)
                    observe(self.store,response.headers,identity,request_id)
                if len(raw) > 262144:
                    raise ProbeError('buffer_response_too_large')
                result = json.loads(raw)
        except ProbeError:
            raise
        except HTTPError as error:
            record_headers(self.store,error.headers)
            observe(self.store,error.headers,identity,request_id)
            raise ProbeError('buffer_http_' + str(error.code)) from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise ProbeError('buffer_response_unknown') from None
        if not isinstance(result, dict) or result.get('errors') or not isinstance(result.get('data'), dict):
            raise ProbeError('buffer_graphql_error')
        return result['data']
