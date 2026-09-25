"""Coordinator around the unchanged single-dataset execution engine.

Steps run with up to pipeline.max_parallel_steps running at once (default 1,
today's exact one-at-a-time behavior). A step that fails "fatally" or under
failure_policy='stop' sets a shared stop flag: any step that has not yet
started its own real work skips itself: any step already in flight when
that happens still runs to completion. Each step's own report entry is
touched only by that step's own thread, so the coordinator's lock is only
needed around the shared report snapshot taken for persist().
"""
import copy
import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import remote
from .engine import execute
from .ordered import KIND, from_dict, to_dict, step_pipeline, preflight, step_spec, STEP_ID
from .queries import QueryError
from .serialization import pipeline_to_dict, json_default
from .spec import ConfigError, require
from .store import now
from .workers import worker_registry


class StateWriteError(RuntimeError):
    pass


def initial_report(spec, run_id):
    return {'kind':KIND,'run_id':run_id,'status':'queued','started':None,'finished':None,
            'steps':[{'id':s['id'],'name':s['name'],'position':i,'status':'pending',
                      'started':None,'finished':None,'processed':0,'valid':0,'invalid':0,
                      'counts_basis':'processed_rows','directory':None,'output':None,
                      'diagnostics':None,'partial_directory':None,'error':None}
                     for i,s in enumerate(spec['steps'],1)]}


def _skip_pending(report,reason):
    for entry in report['steps']:
        if entry['status']=='pending':
            entry.update(status='skipped',finished=now(),error={'code':'STEP_SKIPPED','message':reason})


def interrupted_report(spec, run_id, previous):
    report=copy.deepcopy(previous) if previous.get('kind')==KIND else initial_report(spec,run_id)
    for entry in report['steps']:
        if entry['status']=='running':
            entry.update(status='interrupted',finished=now(),counts_basis='last_checkpoint',
                         error={'code':'STEP_INTERRUPTED','message':'Application stopped; counts are the last persisted checkpoint'})
    _skip_pending(report,'Run interrupted; steps are not resumed automatically')
    report.update(status='interrupted',finished=now())
    return report


def failed_report(spec, run_id, previous, message):
    report=interrupted_report(spec,run_id,previous)
    for entry in report['steps']:
        if entry['status']=='interrupted':
            entry.update(status='failed',error={'code':'COORDINATOR_FAILED','message':message})
    report.update(status='failed',error=message)
    return report


def coordinate(spec, root, output_root, run_id, *, persist):
    pipeline=from_dict(spec)
    snapshot=to_dict(pipeline)
    require(isinstance(run_id,str) and re.fullmatch(r'[0-9a-f]{32}',run_id),'Invalid run ID')
    root=Path(root).resolve(); output_root=Path(output_root).resolve()
    run_root=output_root/run_id
    report=initial_report(snapshot,run_id)
    report.update(status='running',started=now())
    publish_lock=threading.Lock()
    stopped=threading.Event()

    def publish():
        with publish_lock:
            try:persist(copy.deepcopy(report))
            except Exception:
                raise StateWriteError('Ordered run state could not be persisted; execution stopped') from None

    def contained(path):
        require(path.resolve().is_relative_to(run_root.resolve()),'Output escaped the generated run directory')
        return path

    def resolve_target(step):
        """"server"/a named worker pass through; "auto" picks the first idle,
        reachable worker and falls back to "server" when none is available -
        decided per step, at the moment it actually starts, so load already
        placed by earlier steps in this same run is reflected."""
        declared=step.execution_target
        if declared=='server':
            return 'server'
        workers=worker_registry()
        if declared=='auto':
            for name,config in workers.items():
                status=remote.health(config['url'],config['token'],timeout=3)
                if status.get('online') and status.get('idle'):
                    return name
            return 'server'
        require(declared in workers,f"Step '{step.id}': unknown execution target '{declared}'")
        return declared

    def run_remote(worker,job_id,single,timeout=600):
        remote.dispatch(worker['url'],worker['token'],job_id,single)
        deadline=time.monotonic()+timeout
        while True:
            try:
                job=remote.poll(worker['url'],worker['token'],job_id)
            except ConfigError:
                job=None  # Transient network hiccup: retry, subject to the timeout below.
            if job is not None and job['status']!='running':
                break
            require(time.monotonic()<deadline,'Worker did not respond within the timeout')
            time.sleep(1)  # This thread owns the step for its whole duration anyway.
        require(job['status']=='completed',job.get('error') or 'Remote step failed')
        return job['report']

    def run_step(step,entry):
        # Only this call ever mutates this particular entry, so no lock is
        # needed for that - only publish()'s shared report snapshot needs one.
        if stopped.is_set():
            entry.update(status='skipped',finished=now(),error={'code':'STEP_SKIPPED','message':'Earlier step failed; run stopped'})
            publish()
            return
        work=contained(run_root/'.partial'/f"{entry['position']:03d}-{step.id}")
        target=contained(run_root/f"{entry['position']:03d}-{step.id}")
        entry.update(status='running',started=now(),partial_directory=str(work))
        publish()
        try:
            preflight(pipeline,root,output_root,steps=(step,))
            single=pipeline_to_dict(step_pipeline(pipeline,step))
            chosen=resolve_target(step)
            if chosen=='server':
                work.mkdir(parents=True,exist_ok=False)
                def observe(row):
                    entry['processed']+=1
                    entry['valid' if row.valid else 'invalid']+=1
                    if entry['processed'] % 1000 == 0:publish()
                result=execute(single,root,work,on_row=observe)
                directory=contained(Path(result['directory']))
                require(directory.resolve().is_relative_to(work.resolve()),'Invalid engine output directory')
            else:
                # The worker runs this on its own filesystem: its output only
                # becomes usable here if server and worker share the output
                # root (same --data, or a real network share) - same
                # requirement as a top-level run dispatched to a worker.
                worker=worker_registry()[chosen]
                result=run_remote(worker,f'{run_id}:{step.id}',single)
                directory=Path(result['directory'])
            output=f'{step.id}.{step.destination["kind"]}'
            (directory/f'valid.{step.destination["kind"]}').rename(directory/output)
            # Omit row samples from the coordinator report; existing rejection
            # files are the durable per-row diagnostics, not an in-memory set.
            step_report={k:result[k] for k in ('processed','valid','invalid')}
            step_report.update(step_id=step.id,spec=single)
            with (directory/'report.json').open('x',encoding='utf-8') as handle:
                json.dump(step_report,handle,ensure_ascii=False,default=json_default)
            directory.rename(target)  # Publish the closed complete directory.
            entry.update(**{k:result[k] for k in ('processed','valid','invalid')},
                         status='completed',finished=now(),directory=str(target),output=output,
                         diagnostics='rejected.csv',partial_directory=None)
        except StateWriteError:
            # Never start another step when durable state cannot be recorded;
            # a step already in flight elsewhere still finishes on its own.
            stopped.set()
            raise
        except Exception as error:
            code=error.code if isinstance(error,QueryError) else 'STEP_FAILED'
            # Expected source/config errors are already sanitized by the
            # adapters. Unexpected exceptions and filesystem paths stay private.
            message=str(error) if isinstance(error,(ConfigError,csv.Error)) else 'Step execution failed; check input, output storage and configuration'
            entry.update(status='failed',finished=now(),counts_basis='observed_before_failure',
                         error={'code':code,'message':message})
            fatal=isinstance(error,OSError) or not isinstance(error,(ConfigError,ValueError,csv.Error)) or isinstance(error,QueryError) and code not in {'QUERY_TIMEOUT','QUERY_EXECUTION_FAILED','QUERY_SCHEMA_INVALID'}
            if fatal or pipeline.failure_policy=='stop':
                stopped.set()
        except BaseException:
            # KeyboardInterrupt/SystemExit: a ThreadPoolExecutor worker thread
            # does not stop dispatching queued steps just because one raised
            # this - without the flag, an already-queued-but-not-yet-started
            # step would still run for real after an interrupt.
            stopped.set()
            raise
        publish()

    publish()
    try:
        preflight(pipeline,root,output_root)
        run_root.mkdir(parents=True,exist_ok=False)  # A run can never resume/overwrite.
        with ThreadPoolExecutor(max_workers=pipeline.max_parallel_steps) as executor:
            futures=[executor.submit(run_step,step,entry) for step,entry in zip(pipeline.steps,report['steps'])]
        for future in futures:
            future.result()  # Re-raises StateWriteError; a normal step failure is already recorded, not raised.
        statuses={s['status'] for s in report['steps']}
        if 'skipped' in statuses:
            final='failed'  # A stop was triggered; some steps never ran at all.
        elif 'failed' in statuses:
            final='completed_with_errors'
        else:
            final='completed'
        report.update(status=final,finished=now())
    except StateWriteError:
        raise
    except Exception:
        _skip_pending(report,'Run preflight or coordinator failed')
        report.update(status='failed',finished=now(),error='Run preflight or coordinator failed; no later step was started')
    publish()
    return report


def step_history(run, step_id, data_root, *, allow_partial=False):
    """Project a snapshot step for existing diagnostics, never validate/execute SQL."""
    require(run is not None and run['spec'].get('kind')==KIND,'Ordered run not found')
    require(isinstance(step_id,str) and STEP_ID.fullmatch(step_id),'Invalid step ID')
    index=next((i for i,s in enumerate(run['spec']['steps']) if s['id']==step_id),None)
    require(index is not None,'Step not found')
    entries=run['report'].get('steps',[])
    require(len(entries)>index and entries[index]['id']==step_id,'Step report is unavailable')
    entry=entries[index]
    base=(Path(data_root)/'runs'/run['id']).resolve()
    completed=entry['status']=='completed'
    require(completed or allow_partial and entry['status'] in ('failed','interrupted'),'Step has no completed output')
    if completed:
        directory=base/f'{index+1:03d}-{step_id}'
    else:
        work=base/'.partial'/f'{index+1:03d}-{step_id}'
        directories=list(work.glob('*')) if work.is_dir() else []
        directories=[p for p in directories if p.is_dir() and re.fullmatch(r'[0-9a-f]{32}',p.name)]
        require(len(directories)==1,'Partial diagnostics are unavailable')
        directory=directories[0]
    require(directory.resolve().is_relative_to(base) and base.is_relative_to((Path(data_root)/'runs').resolve()),'Invalid stored output path')
    single=step_spec(run['spec']['connection_env'],run['spec']['steps'][index])
    return {'id':run['id']+':'+step_id,'name':run['name']+' / '+entry['name'],
            'status':'completed','spec':single,'report':{'directory':str(directory)},
            'step_status':entry['status'],'partial':not completed}


def download_path(run, step_id, filename, data_root):
    projected=step_history(run,step_id,data_root)
    extension=projected['spec']['destination']['kind']
    require(filename in (f'{step_id}.{extension}','rejected.csv','rejected.xlsx','report.json'),'Output file not found')
    path=(Path(projected['report']['directory'])/filename).resolve()
    require(path.is_relative_to(Path(projected['report']['directory']).resolve()) and path.is_file(),'Output file not found')
    return path
