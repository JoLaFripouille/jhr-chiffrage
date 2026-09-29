"""CCTP adaptation copies lots/ouvrages into reviewed, atomic draft updates."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError
from jhr_chiffrage.core import Store, new_item, DomainError
from jhr_chiffrage.agent_workflow import AgentPlans, Change, outline, find_reusable


@pytest.fixture
def example(tmp_path):
    store=Store(tmp_path/'data.sqlite')
    settings=store.get_settings()
    settings.update(hourly_rate='60',levy_rate='20',vat_enabled=False)
    store.save_settings(settings,settings['revision'])
    source=store.create_estimate('Models')
    root,view,detail=[new_item(name) for name in ('Chassis','Coupe','Cotation')]
    for item,minutes in zip((root,view,detail),(0,20,10)):
        item.update(duration_minutes=minutes,cctp_reference='Old document',notes='Reusable detail')
    view['parent_id']=root['id']
    detail['parent_id']=view['id']
    source['works']=[{'id':str(uuid4()),'name':'LOT DEMO','items':[root,view,detail]}]
    source=store.save_estimate(source,source['revision'])
    target=store.create_estimate('Target')
    return store,source,target


def actions(source):
    work=source['works'][0]
    root,view,detail=work['items']
    return [
        {'action':'add_lot','key':'@lot','name':'LOT 02'},
        {'action':'copy_ouvrage','key':'@ouvrage','lot_id':'@lot','name':'C2 - Chassis adapted',
         'source':{'estimate_id':source['id'],'revision':source['revision'],'lot_id':work['id'],'item_id':root['id']},
         'fields':{'cctp_reference':'Example CCTP, section 2.3 p.12','time_basis':'Own effort zero; detailed below'}},
        {'action':'update_post','lot_id':'@lot','item_id':'@ouvrage/'+detail['id'],
         'fields':{'duration_minutes':15,'quantity':'2','time_basis':'Estimated 15 min per view, 2 views','cctp_reference':'Example CCTP p.12'}},
        {'action':'add_post','key':'@check','lot_id':'@lot','parent_id':'@ouvrage/'+detail['id'],
         'fields':{'label':'Check','duration_minutes':5,'time_basis':'Estimated review time'}},
    ]


def test_preview_apply_replay_and_outline(example):
    store,source,target=example
    plans=AgentPlans(store)
    preview=plans.prepare(target['id'],target['revision'],[Change(**a) for a in actions(source)],'Adapt from example CCTP')
    assert store.get_estimate(target['id'])==target
    assert preview['after']['ht_cents']==5500
    assert len(preview['changes']['added_posts'])==4
    assert plans.read(preview['plan_id'])['preview']==preview
    applied=plans.apply(preview['plan_id'])
    assert applied['saved'] and applied['revision']==target['revision']+1
    assert AgentPlans(Store(store.path)).apply(preview['plan_id'])==applied
    saved=store.get_estimate(target['id'])
    assert store.get_estimate(source['id'])==source
    original_ids={i['id'] for i in source['works'][0]['items']}
    assert not original_ids.intersection(i['id'] for i in saved['works'][0]['items'])
    root=outline(saved)['lots'][0]['ouvrages'][0]
    assert root['own_ht_cents']==0 and root['subtree_ht_cents']==5500
    assert root['children'][0]['children'][0]['children'][0]['label']=='Check'
    assert root['cctp_reference']=='Example CCTP, section 2.3 p.12'
    assert root['origin']['estimate_id']==source['id']
    assert 'cctp_reference' not in root['children'][0]
    assert 'à vérifier' in root['children'][0]['time_basis']


def test_copy_legacy_tab_into_ouvrage_and_whole_lot(example):
    store,source,target=example
    source_ref={'estimate_id':source['id'],'revision':source['revision'],'lot_id':source['works'][0]['id']}
    changes=[Change(action='copy_lot',key='@lot',name='Copied lot',source=source_ref),
             Change(action='copy_ouvrage',lot_id='@lot',name='Legacy work regrouped',key='@legacy',source=source_ref)]
    plans=AgentPlans(store)
    preview=plans.prepare(target['id'],target['revision'],changes,'Reuse entire tab')
    plans.apply(preview['plan_id'])
    saved=store.get_estimate(target['id'])
    roots=outline(saved)['lots'][0]['ouvrages']
    assert len(roots)==2
    assert roots[1]['label']=='Legacy work regrouped'
    assert roots[1]['own_ht_cents']==0 and roots[1]['subtree_ht_cents']==3000
    assert saved['settings']==target['settings']


def test_stale_source_target_and_frozen_are_rejected(example):
    store,source,target=example
    plans=AgentPlans(store)
    change=Change(action='add_lot',name='New lot')
    preview=plans.prepare(target['id'],target['revision'],[change],'Draft')
    updated=store.save_estimate(dict(target,name='Other PC'),target['revision'])
    with pytest.raises(DomainError,match='changé'):
        plans.apply(preview['plan_id'])
    assert store.get_estimate(target['id'])==updated
    store.save_estimate(dict(source,name='Changed model'),source['revision'])
    with pytest.raises(DomainError,match='source'):
        plans.prepare(target['id'],updated['revision'],[Change(**a) for a in actions(source)],'Draft')
    source=store.get_estimate(source['id'])
    frozen=store.freeze_estimate(source['id'],source['revision'])
    with pytest.raises(DomainError,match='figée'):
        plans.prepare(frozen['id'],frozen['revision'],[change],'Draft')


def test_invalid_late_action_never_partially_writes(example):
    store,source,target=example
    changes=[Change(**a) for a in actions(source)]
    changes.append(Change(action='move_post',lot_id='@lot',item_id='@ouvrage',target_lot_id='@lot',target_item_id='@check',placement='inside'))
    with pytest.raises(DomainError):
        AgentPlans(store).prepare(target['id'],target['revision'],changes,'Cycle is invalid')
    assert store.get_estimate(target['id'])==target
    assert not list(AgentPlans(store).folder.glob('*.json'))


def test_plans_are_scoped_and_search_is_read_only(example,tmp_path):
    store,source,target=example
    assert find_reusable(store,'chassis')[0]['item_id']==source['works'][0]['items'][0]['id']
    plans=AgentPlans(store)
    p=plans.prepare(target['id'],target['revision'],[Change(action='add_lot',name='Lot')],'Example')
    other=AgentPlans(Store(tmp_path/'other.sqlite'))
    with pytest.raises(DomainError):other.apply(p['plan_id'])
    with pytest.raises(DomainError):plans.read('../../data.sqlite')
    with pytest.raises(ValidationError):Change(action='add_lot',name='Lot',item_id='ignored-field')
    with pytest.raises(ValidationError):Change(action='add_post',lot_id='x',fields={'label':'a','duration_minutes':True})


def unpack(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


def test_real_stdio_agent_workflow_and_read_profile(example):
    store,source,target=example
    async def run():
        env=dict(os.environ,JHR_CHIFFRAGE_DB=str(store.path),JHR_MCP_ACCESS='draft')
        for key in ('JHR_SERVER_URL','JHR_SERVER_TOKEN','JHR_SERVER_CA'):env.pop(key,None)
        params=StdioServerParameters(command=sys.executable,args=['-m','jhr_chiffrage.mcp_server'],env=env)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                hello=await session.initialize()
                assert 'lots' in hello.instructions
                names={t.name for t in (await session.list_tools()).tools}
                assert {'prepare_estimate_changes','apply_estimate_plan','get_estimate_outline','search_reusable_ouvrages'} <= names
                preview=unpack(await session.call_tool('prepare_estimate_changes',{'estimate_id':target['id'],'expected_revision':target['revision'],'changes':actions(source),'context':'Example CCTP section 2.3'}))
                result=unpack(await session.call_tool('apply_estimate_plan',{'plan_id':preview['plan_id']}))
                assert result['totals']['ht_cents']==5500
                assert unpack(await session.call_tool('apply_estimate_plan',{'plan_id':preview['plan_id']}))==result
                saved=unpack(await session.call_tool('get_estimate',{'estimate_id':target['id']}))
                # Existing whole-estimate tool must preserve evidence from the new tools.
                saved['client']='Test'
                res=unpack(await session.call_tool('save_estimate',{'data':saved,'expected_revision':saved['revision'],'operation_id':str(uuid4())}))
                assert res['works'][0]['items'][0]['origin']['estimate_id']==source['id']
        env['JHR_MCP_ACCESS']='read'
        async with stdio_client(StdioServerParameters(command=sys.executable,args=params.args,env=env)) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                names={t.name for t in (await session.list_tools()).tools}
                assert 'get_estimate_outline' in names
                assert not {'prepare_estimate_changes','apply_estimate_plan'}.intersection(names)
                assert (await session.call_tool('apply_estimate_plan',{'plan_id':preview['plan_id']})).isError
    asyncio.run(run())


def test_unsaved_human_work_blocks_agent_preparation_and_application(example):
    from jhr_chiffrage.recovery import RecoveryFile
    store,source,target=example
    plans=AgentPlans(store)
    change=Change(action='add_lot',name='Lot')
    preview=plans.prepare(target['id'],target['revision'],[change],'Example')
    recovery=RecoveryFile(store)
    recovery.write({'estimate':dict(target,name='Unsaved human work')})
    with pytest.raises(DomainError) as err:
        plans.prepare(target['id'],target['revision'],[change],'Example')
    assert err.value.code=='UNSAVED_LOCAL_CHANGES'
    with pytest.raises(DomainError):plans.apply(preview['plan_id'])
    assert store.get_estimate(target['id'])==target
