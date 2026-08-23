import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, Download, Film, FolderOpen, Trash2 } from 'lucide-react';
import { apiJson } from '../lib/api';

// The library works in both modes: private signed links in cloud mode, and
// local /videos links plus cleanup controls in self-host mode. Videos are
// grouped by project (job); re-openable cloud projects can be restored for
// further editing in the Clip Generator.
export default function HistoryTab({ onReopenProject, onLocalDelete, localMode = false }) {
  const [videos, setVideos] = useState(null);
  const [projects, setProjects] = useState({});
  const [reopening, setReopening] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [reopenError, setReopenError] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const [deleteNotice, setDeleteNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await apiJson(localMode ? '/api/local/history' : '/api/history');
        if (cancelled) return;
        setVideos(data.videos || []);
        const map = {};
        for (const p of data.projects || []) map[p.job_id] = p;
        setProjects(map);
        if (!localMode) {
          apiJson('/api/projects')
            .then((d) => {
              if (cancelled) return;
              const cloudMap = {};
              for (const p of d.projects || []) cloudMap[p.job_id] = p;
              setProjects(cloudMap);
            })
            .catch(() => {});
        }
      } catch (_) {
        if (!cancelled) setError(localMode ? 'Could not load local clips.' : 'Could not load your library.');
      }
    };
    load();
    return () => { cancelled = true; };
  }, [localMode]);

  // Group videos by job, preserving the newest-first order of /api/history.
  const groups = useMemo(() => {
    const byJob = new Map();
    for (const v of videos || []) {
      const key = v.job_id || v.id;
      if (!byJob.has(key)) byJob.set(key, []);
      byJob.get(key).push(v);
    }
    return [...byJob.entries()];
  }, [videos]);

  const handleReopen = async (jobId) => {
    if (!onReopenProject || reopening) return;
    setReopening(jobId);
    setReopenError('');
    try {
      await onReopenProject(jobId);
    } catch (e) {
      setReopenError('Could not reopen this project. Please try again.');
      setReopening(null);
    }
  };

  const handleDelete = async (jobId, title) => {
    if (!localMode || deleting) return;
    const confirmed = window.confirm(
      `Delete all local files for “${title || 'this project'}”? This removes the generated clips and the source upload owned by this job.`
    );
    if (!confirmed) return;
    setDeleting(jobId);
    setDeleteError('');
    setDeleteNotice('');
    try {
      const result = await apiJson(`/api/local/history/${jobId}`, { method: 'DELETE' });
      setVideos((current) => (current || []).filter((video) => video.job_id !== jobId));
      setProjects((current) => {
        const next = { ...current };
        delete next[jobId];
        return next;
      });
      onLocalDelete?.(jobId);
      const megabytes = ((result.deleted_bytes || 0) / 1024 / 1024).toFixed(1);
      setDeleteNotice(`Deleted ${jobId}. Freed ${megabytes} MB.`);
    } catch (e) {
      setDeleteError(e?.detail || 'Could not delete this local project.');
    } finally {
      setDeleting(null);
    }
  };

  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '');

  if (videos === null && !error) {
    return <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>;
  }

  return (
    <div className="h-full overflow-y-auto p-8 max-w-5xl mx-auto animate-fade">
      <p className="eyebrow mb-1.5">06 · {localMode ? 'LOCAL LIBRARY' : 'HISTORY'}</p>
      <h1 className="font-display text-2xl text-ink mb-2">{localMode ? 'Local Library' : 'Your Library'}</h1>
      <p className="text-muted text-sm mb-8">
        {localMode
          ? 'Generated clips stored on this machine. Delete a project to remove its output files and owned source upload from disk.'
          : "All the shorts you've generated, saved while your plan is active. Kept for 7 days after your plan ends. Reopen a project to keep editing its clips."}
      </p>

      {error && <p className="text-danger text-sm">{error}</p>}
      {reopenError && <p className="text-danger text-sm mb-4">{reopenError}</p>}
      {deleteError && <p className="text-danger text-sm mb-4">{deleteError}</p>}
      {deleteNotice && <p className="text-ok text-sm mb-4">{deleteNotice}</p>}

      {videos && videos.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Film size={40} className="mx-auto mb-4 text-muted" />
          <p>No videos yet. Generate your first short from the Clip Generator.</p>
        </div>
      )}

      <div className="space-y-10">
        {groups.map(([jobId, vids]) => {
          const project = projects[jobId];
          return (
            <section key={jobId}>
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4 pb-2 border-b border-rule">
                <div className="min-w-0">
                  <p className="text-sm text-ink font-medium truncate" title={project?.title || vids[0]?.title}>
                    {project?.title || vids[0]?.title || 'Project'}
                  </p>
                  <p className="readout mt-0.5">
                    {fmtDate(vids[0]?.created_at)} · {vids.length} clip{vids.length === 1 ? '' : 's'}
                  </p>
                </div>
                {project && onReopenProject && (
                  <button
                    onClick={() => handleReopen(jobId)}
                    disabled={!!reopening}
                    className="btn-ghost px-3 py-2 text-xs shrink-0"
                    title="Restore this project in the Clip Generator to keep editing subtitles, hooks, effects and dubbing"
                  >
                    {reopening === jobId
                      ? <><Loader2 size={14} className="animate-spin" /> reopening…</>
                      : <><FolderOpen size={14} /> reopen project</>}
                  </button>
                )}
                {localMode && (
                  <button
                    onClick={() => handleDelete(jobId, project?.title || vids[0]?.title)}
                    disabled={!!deleting}
                    className="btn-danger px-3 py-2 text-xs shrink-0"
                    title="Delete this job's generated files and owned source upload"
                  >
                    {deleting === jobId
                      ? <><Loader2 size={14} className="animate-spin" /> deleting…</>
                      : <><Trash2 size={14} /> Delete Files</>}
                  </button>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-5">
                {vids.map((v) => (
                  <div key={v.id} className="card card-hover overflow-hidden group">
                    <div className="aspect-[9/16] bg-black">
                      <video src={v.view_url} controls preload="metadata" className="w-full h-full object-contain" />
                    </div>
                    <div className="p-3">
                      <p className="text-sm text-ink font-medium line-clamp-2 mb-1" title={v.title}>{v.title || 'Short'}</p>
                      <div className="flex items-center justify-between">
                        <span className="readout">{fmtDate(v.created_at)}</span>
                        <a href={v.download_url} className="text-micro font-mono uppercase text-brass hover:text-ink flex items-center gap-1 transition-colors" title="Download">
                          <Download size={14} /> Download
                        </a>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
