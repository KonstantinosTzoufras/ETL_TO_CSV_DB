"""Separate codec for ordered independent query exports; no row processing."""
import re
from pathlib import Path

from .models import OrderedQueryPipeline, QueryExportStep, Pipeline, SourceDefinition
from .queries import approves, authorize, connection_policies, query_to_dict, QueryError
from .serialization import pipeline_from_dict, pipeline_to_dict
from .spec import require, keys, validate
from .sources import input_path

KIND = 'ordered_query_export'
STEP_ID = re.compile(r'[a-z][a-z0-9_-]{0,47}')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1,10)), *(f'lpt{i}' for i in range(1,10))}


def is_ordered(spec):
    return isinstance(spec, dict) and spec.get('kind') == KIND


def step_spec(connection_env, step):
    """Historical projection also works without reparsing/reapproving query SQL."""
    return {'version': step['processing_version'], 'name': step['name'],
            'source': {'kind': 'sqlserver_query', 'connection_env': connection_env, 'query': step['query']},
            'columns': step['columns'], 'destination': step['destination']}


def step_pipeline(pipeline, step):
    return Pipeline(step.name, SourceDefinition('sqlserver_query', {
        'connection_env': pipeline.connection_env, 'query': step.query}), step.columns, step.destination, step.processing_version)


def from_dict(spec):
    keys(spec, {'kind','format_version','name','connection_env','failure_policy','max_parallel_steps','steps'}, 'ordered pipeline')
    require(spec.get('kind') == KIND and type(spec.get('format_version')) is int and spec['format_version'] == 1, 'Unsupported ordered pipeline format')
    require(isinstance(spec.get('name'),str) and 0 < len(spec['name'].strip()) <= 120, 'Pipeline name must contain 1-120 characters')
    policy=spec.get('failure_policy','stop')
    require(isinstance(policy,str) and policy in ('stop','continue'), 'Failure policy must be stop or continue')
    # Every step shares one connection_env, so this bounds how many
    # simultaneous connections/queries the target SQL Server sees from this
    # run - default 1 keeps today's strictly one-at-a-time behavior.
    max_parallel=spec.get('max_parallel_steps',1)
    require(type(max_parallel) is int and 1 <= max_parallel <= 8, 'max_parallel_steps must be an integer between 1 and 8')
    raw_steps=spec.get('steps')
    require(isinstance(raw_steps,list) and 1 <= len(raw_steps) <= 20, 'Choose 1-20 ordered steps')
    steps=[]; seen=set()
    for raw in raw_steps:
        keys(raw, {'id','name','query','processing_version','columns','destination'}, 'query step')
        require(set(raw)=={'id','name','query','processing_version','columns','destination'}, 'Each step requires id, name, query, processing_version, columns and destination')
        identifier=raw['id']
        require(isinstance(identifier,str) and STEP_ID.fullmatch(identifier) and identifier not in RESERVED, 'Step ID must be a safe lowercase identifier (1-48 characters), not a reserved filename')
        require(identifier not in seen,'Step IDs must be unique');seen.add(identifier)
        # download_path below names a step's file as f'{step_id}.{extension}', so
        # a per-group subdirectory has nowhere to be served from yet.
        require(not isinstance(raw.get('destination'),dict) or 'split_by' not in raw['destination'], 'split_by is not supported for ordered query steps')
        # A database export claims a table by the pipeline's own stable id; a
        # step has no such identity of its own to claim one with.
        require(not isinstance(raw.get('destination'),dict) or raw['destination'].get('kind')!='sqlserver', 'Database export is not supported for ordered query steps')
        single=pipeline_from_dict(step_spec(spec.get('connection_env'),raw))
        for column in raw['columns']:
            lookup=column.get('lookup',{}).get('source')
            if lookup and lookup['kind']=='sqlserver':
                require(lookup['connection_env']==spec['connection_env'], 'SQL lookups must use the shared connection')
        steps.append(QueryExportStep(identifier,single.name,single.source.options['query'],single.columns,single.destination,single.version))
    return OrderedQueryPipeline(spec['name'],spec['connection_env'],tuple(steps),policy,max_parallel_steps=max_parallel)


def to_dict(pipeline):
    require(isinstance(pipeline,OrderedQueryPipeline),'Expected OrderedQueryPipeline')
    steps=[]
    for step in pipeline.steps:
        require(isinstance(step,QueryExportStep),'Expected QueryExportStep')
        single=pipeline_to_dict(step_pipeline(pipeline,step))
        steps.append({'id':step.id,'name':step.name,'query':single['source']['query'],
                      'processing_version':single['version'],'columns':single['columns'],'destination':single['destination']})
    spec={'kind':KIND,'format_version':pipeline.format_version,'name':pipeline.name,
          'connection_env':pipeline.connection_env,'failure_policy':pipeline.failure_policy,
          'max_parallel_steps':pipeline.max_parallel_steps,'steps':steps}
    from_dict(spec)
    return spec


def validate_definition(spec):
    if is_ordered(spec):
        from_dict(spec)
        return spec
    return validate(spec)


def preflight(pipeline, root, output_root, *, steps=None):
    """No SQL execution: validate approval, static lookup paths, and object scope."""
    for step in pipeline.steps if steps is None else steps:
        authorize(pipeline.connection_env,step.query)
        for column in pipeline_to_dict(step_pipeline(pipeline,step))['columns']:
            source=column.get('lookup',{}).get('source')
            if not source:continue
            if source['kind'] in ('csv','xml'):
                path=input_path(root,source['path'],extension='.xml' if source['kind']=='xml' else '.csv')
                require(not path.is_relative_to(Path(output_root).resolve()), 'Run outputs cannot be lookup inputs')
            else:
                require(source['kind']=='sqlserver' and source['connection_env']==pipeline.connection_env, 'Only static lookups on the shared connection are supported')
                policy=connection_policies().get(pipeline.connection_env)
                if policy is None or not approves(policy,[source['schema'],source['table']]):
                    raise QueryError('QUERY_PERMISSION_DENIED','SQL lookup object is not approved')


def preview_spec(spec, step_id, root, output_root):
    require(is_ordered(spec) and isinstance(spec.get('steps'),list), 'Expected ordered pipeline')
    matches=[s for s in spec['steps'] if isinstance(s,dict) and s.get('id')==step_id]
    require(len(matches)==1,'Select one unique step for preview')
    pipeline=from_dict({**spec,'steps':matches})
    selected=next((s for s in pipeline.steps if s.id==step_id),None)
    require(selected is not None,'Select one step for preview')
    preflight(pipeline,root,output_root,steps=(selected,))
    return pipeline_to_dict(step_pipeline(pipeline,selected))
