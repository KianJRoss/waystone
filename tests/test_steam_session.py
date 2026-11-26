import base64,json,struct
import pytest
from native_services.steam_session import SteamQrSession,SteamTokenAdapter,integer,blob,decode
OWNER='76561197968575517';FRIEND='76561197968575518'
class Response:
 def __init__(self,value=b'',data=None):self.content=value if data is None else json.dumps(data).encode();self.headers={'x-eresult':'1'};self.data=data
 def raise_for_status(self):pass
 def json(self):return self.data

def token():return 'head.'+base64.urlsafe_b64encode(json.dumps({'sub':OWNER}).encode()).decode().rstrip('=')+'.signature'

def test_explicit_qr_auth_protocol_no_startup_calls_and_rate_limited_poll():
 calls=[];now=[0.]
 def request(method,url,**kw):
  calls.append((method,url,kw));assert method=='POST' and kw['follow_redirects'] is False
  payload=decode(base64.b64decode(kw['data']['input_protobuf_encoded']))
  if 'BeginAuth' in url:
   assert payload[1]==[b'Waystone on Linux'] and payload[4]==[b'Client']
   return Response(integer(1,77)+blob(2,'https://s.team/q/1/77')+blob(3,b'private-request')+bytes([37])+struct.pack('<f',5))
  assert payload[1]==[77] and payload[2]==[b'private-request']
  return Response(blob(3,token())+blob(4,'private-access'))
 session=SteamQrSession(request=request,clock=lambda:now[0]);assert calls==[]
 result=session.begin();assert result['interval']==5 and len(calls)==1
 assert session.poll() is None and len(calls)==1
 now[0]=5;adapter=session.poll();assert adapter.account_id==OWNER and len(calls)==2 and session.challenge_url==''
 assert 'private-access' not in repr(adapter)

def test_cancel_expire_and_malformed_auth_cannot_link():
 session=SteamQrSession();session.cancel()
 with pytest.raises(ValueError):session.poll()
 for data in (b'\x80',b'\x00',b'\x0a\xff',b'\xff'*20):
  with pytest.raises(ValueError):decode(data)
 def request(*a,**kw):return Response(integer(1,77)+blob(2,'https://evil.example/qr')+blob(3,b'x'))
 with pytest.raises(ValueError):SteamQrSession(request=request).begin()

def test_token_friends_and_achievements_real_service_shapes_unknown_not_locked():
 calls=[]
 def request(url,**kw):
  calls.append((url,kw));assert kw['params']['access_token']=='private-access'
  if 'GetFriendsList' in url:return Response(data={'response':{'friendslist':{'friends':[{'ulfriendid':FRIEND,'efriendrelationship':3}]}}})
  if 'GetPlayerLinkDetails' in url:return Response(data={'response':{'accounts':[{'public_data':{'steamid':FRIEND,'persona_name':'Friend'},'private_data':{'persona_state':1}}]}})
  if 'GetGameAchievements' in url:return Response(data={'response':{'achievements':[{'internal_key':1,'localized_name':'First'},{'internal_key':2,'localized_name':'Missing observation'}]}})
  return Response(data={'response':{'achievements':[{'internal_key':1,'unlocked':True,'unlock_time':0}]}})
 adapter=SteamTokenAdapter(OWNER,'private-access',request=request);assert not calls
 friends=adapter.fetch_friends();assert friends['friends'][0]['online'] is True
 result=adapter.fetch_achievements('game','440');assert result['items'][1]['unlocked'] is None
 assert result['completion_percent'] is None and result['unlocked_count']==1
 assert 'private-access' not in json.dumps(result)

def test_token_request_errors_never_expose_access_token():
 def request(*a,**kw):raise RuntimeError('url?access_token=private-access')
 with pytest.raises(ValueError) as e:SteamTokenAdapter(OWNER,'private-access',request=request).fetch_friends()
 assert 'private-access' not in str(e.value)

def test_restore_refreshes_without_replaying_qr_and_keeps_rotated_refresh():
 old=token();new='rotated.'+old.split('.')[1]+'.signed';calls=[]
 def request(method,url,**kw):
  calls.append(url);assert 'GenerateAccessTokenForApp' in url
  payload=decode(base64.b64decode(kw['data']['input_protobuf_encoded']))
  assert payload[1]==[old.encode()] and struct.unpack('<Q',payload[2][0])[0]==int(OWNER) and payload[3]==[1]
  return Response(blob(1,'fixture-new-access')+blob(2,new))
 session=SteamQrSession(request=request)
 adapter=session.restore(OWNER,old)
 assert adapter.session_record()=={'kind':'steam_refresh','account_id':OWNER,'refresh_token':new}
 assert len(calls)==1 and 'fixture-new-access' not in repr(adapter)

def test_restore_refuses_account_mismatch_before_any_network():
 calls=[];session=SteamQrSession(request=lambda *a,**kw:calls.append(a))
 with pytest.raises(ValueError):session.restore(FRIEND,token())
 assert not calls

def test_restore_rejection_never_exposes_saved_secret():
 def request(*a,**kw):raise RuntimeError('private-refresh:'+token())
 with pytest.raises(ValueError) as exc:SteamQrSession(request=request).restore(OWNER,token())
 assert token() not in str(exc.value)
