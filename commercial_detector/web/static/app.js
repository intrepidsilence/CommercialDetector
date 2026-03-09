// Main JavaScript for CommercialDetector Dashboard

document.addEventListener('DOMContentLoaded', () => {

  // ----------------------------------------------------------------
  // Constants
  // ----------------------------------------------------------------

  const SCORE_MIN = -10;
  const SCORE_MAX = 15;
  const SCORE_RANGE = SCORE_MAX - SCORE_MIN;
  const THRESH_COMMERCIAL = 8.0;
  const THRESH_PROGRAM = -3.0;
  const SSE_RECONNECT_MS = 3000;

  const SIGNAL_WEIGHTS = {
    silence_end:            5.0,
    black_start:            4.0,
    loudness_shift:         4.0,
    scene_change:           0.5,
    transcript_commercial:  3.0,
    transcript_program:    -4.0,
  };

  // ----------------------------------------------------------------
  // Sidebar toggle (mobile)
  // ----------------------------------------------------------------

  const sidebarToggle = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
    document.addEventListener('click', (e) => {
      if (sidebar.classList.contains('open') &&
          !sidebar.contains(e.target) &&
          e.target !== sidebarToggle) {
        sidebar.classList.remove('open');
      }
    });
  }

  // ----------------------------------------------------------------
  // SSE Connection
  // ----------------------------------------------------------------

  let eventSource = null;
  let sseRetryTimer = null;

  function connectSSE() {
    if (eventSource) {
      eventSource.close();
    }
    eventSource = new EventSource('/api/events');

    eventSource.addEventListener('signal', (e) => {
      try {
        const data = JSON.parse(e.data);
        handleSignalEvent(data);
      } catch (err) { console.error('SSE signal parse error', err); }
    });

    eventSource.addEventListener('transition', (e) => {
      try {
        const data = JSON.parse(e.data);
        handleTransitionEvent(data);
      } catch (err) { console.error('SSE transition parse error', err); }
    });

    eventSource.addEventListener('state', (e) => {
      try {
        const data = JSON.parse(e.data);
        handleStateEvent(data);
      } catch (err) { console.error('SSE state parse error', err); }
    });

    eventSource.onerror = () => {
      eventSource.close();
      eventSource = null;
      if (!sseRetryTimer) {
        sseRetryTimer = setTimeout(() => {
          sseRetryTimer = null;
          connectSSE();
        }, SSE_RECONNECT_MS);
      }
    };
  }

  connectSSE();

  // ----------------------------------------------------------------
  // Periodic snapshot fetch
  // ----------------------------------------------------------------

  function fetchSnapshot() {
    fetch('/api/snapshot')
      .then(r => r.json())
      .then(data => {
        updateStateBadges(data.state);
        updateScoreDisplay(data.score);
        updateSignalCounts(data.signal_counts);
        updateUptime(data.uptime);
        if (data.mqtt) updateMqttStatus(data.mqtt);
      })
      .catch(() => {});
  }

  fetchSnapshot();
  setInterval(fetchSnapshot, 2000);

  // ----------------------------------------------------------------
  // State updates
  // ----------------------------------------------------------------

  function handleStateEvent(data) {
    if (data.state) updateStateBadges(data.state);
    if (data.score !== undefined) updateScoreDisplay(data.score);
  }

  function updateStateBadges(state) {
    const stateUpper = state.toUpperCase();
    const stateClass = 'state-' + state.toLowerCase();

    const headerBadge = document.getElementById('header-state-badge');
    if (headerBadge) {
      headerBadge.textContent = stateUpper;
      headerBadge.className = 'state-badge state-badge-lg ' + stateClass;
    }

    const sub = document.getElementById('state-sub');
    if (sub) {
      const msgs = {
        program: 'Regular programming detected',
        commercial: 'Commercial break in progress',
        unknown: 'Waiting for data\u2026',
      };
      sub.textContent = msgs[state.toLowerCase()] || '';
    }
  }

  function updateScoreDisplay(score) {
    const scoreVal = document.getElementById('score-value');
    if (scoreVal) scoreVal.textContent = score.toFixed(1);

    const headerScore = document.getElementById('header-score');
    if (headerScore) headerScore.textContent = score.toFixed(1);

    const pct = ((score - SCORE_MIN) / SCORE_RANGE) * 100;
    const clamped = Math.max(0, Math.min(100, pct));
    const pointer = document.getElementById('score-pointer');
    if (pointer) pointer.style.left = clamped + '%';

    if (scoreVal) {
      if (score >= THRESH_COMMERCIAL) scoreVal.style.color = '#ef4444';
      else if (score <= THRESH_PROGRAM) scoreVal.style.color = '#22c55e';
      else scoreVal.style.color = '#e1e4ed';
    }
  }

  function updateSignalCounts(counts) {
    if (!counts) return;
    for (const [type, count] of Object.entries(counts)) {
      const el = document.getElementById('count-' + type);
      if (el) el.textContent = count;
    }
  }

  function updateUptime(seconds) {
    const el = document.getElementById('uptime-value');
    if (el) el.textContent = formatUptime(seconds);
  }

  function updateMqttStatus(mqtt) {
    const dot = document.getElementById('mqtt-dot');
    const label = document.getElementById('mqtt-label');
    if (!dot || !label) return;

    if (mqtt.connected) {
      dot.className = 'mqtt-dot mqtt-dot-connected';
      if (label) label.textContent = 'MQTT';
    } else {
      dot.className = 'mqtt-dot mqtt-dot-error';
      if (label) label.textContent = 'MQTT';
    }
  }

  // ----------------------------------------------------------------
  // Signal events
  // ----------------------------------------------------------------

  let signalPaused = false;
  const pauseBtn = document.getElementById('signal-pause-btn');
  if (pauseBtn) {
    pauseBtn.addEventListener('click', () => {
      signalPaused = !signalPaused;
      pauseBtn.textContent = signalPaused ? 'Resume' : 'Pause';
      pauseBtn.classList.toggle('active', signalPaused);
    });
  }

  function handleSignalEvent(data) {
    // Update counter on dashboard
    const countEl = document.getElementById('count-' + data.type);
    if (countEl) {
      countEl.textContent = parseInt(countEl.textContent || '0') + 1;
    }

    // Append to signal table if on signals page and not paused
    const tbody = document.getElementById('signal-tbody');
    if (tbody && !signalPaused) {
      const tr = document.createElement('tr');
      tr.className = 'signal-row';
      tr.dataset.type = data.type;

      const weight = SIGNAL_WEIGHTS[data.type] || 0;

      // Build cells safely using DOM methods
      const tdTime = document.createElement('td');
      tdTime.className = 'mono';
      tdTime.textContent = data.timestamp != null ? data.timestamp.toFixed(2) + 's' : '--';
      tr.appendChild(tdTime);

      const tdType = document.createElement('td');
      const typeBadge = document.createElement('span');
      typeBadge.className = 'signal-type-badge signal-' + data.type;
      typeBadge.textContent = data.type;
      tdType.appendChild(typeBadge);
      tr.appendChild(tdType);

      const tdValue = document.createElement('td');
      tdValue.className = 'mono';
      tdValue.textContent = data.value != null ? data.value.toFixed(3) : '--';
      tr.appendChild(tdValue);

      const tdWeight = document.createElement('td');
      tdWeight.className = 'mono';
      tdWeight.textContent = (weight >= 0 ? '+' : '') + weight.toFixed(1);
      tr.appendChild(tdWeight);

      // Apply current filters
      if (!isSignalVisible(data.type)) {
        tr.classList.add('hidden');
      }

      tbody.insertBefore(tr, tbody.firstChild);

      // Trim to 100 rows
      while (tbody.children.length > 100) {
        tbody.removeChild(tbody.lastChild);
      }
    }
  }

  // ----------------------------------------------------------------
  // Signal filtering
  // ----------------------------------------------------------------

  let activeFilters = new Set(['all']);

  function isSignalVisible(type) {
    return activeFilters.has('all') || activeFilters.has(type);
  }

  const filterBtns = document.querySelectorAll('.filter-btn');
  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const filter = btn.dataset.filter;

      if (filter === 'all') {
        activeFilters.clear();
        activeFilters.add('all');
        filterBtns.forEach(b => b.classList.toggle('active', b.dataset.filter === 'all'));
      } else {
        activeFilters.delete('all');
        const allBtn = document.querySelector('.filter-btn[data-filter="all"]');
        if (allBtn) allBtn.classList.remove('active');

        if (activeFilters.has(filter)) {
          activeFilters.delete(filter);
          btn.classList.remove('active');
          if (activeFilters.size === 0) {
            activeFilters.add('all');
            if (allBtn) allBtn.classList.add('active');
          }
        } else {
          activeFilters.add(filter);
          btn.classList.add('active');
        }
      }

      applySignalFilters();
    });
  });

  function applySignalFilters() {
    const rows = document.querySelectorAll('.signal-row');
    rows.forEach(row => {
      row.classList.toggle('hidden', !isSignalVisible(row.dataset.type));
    });
  }

  // ----------------------------------------------------------------
  // Transition events
  // ----------------------------------------------------------------

  function handleTransitionEvent(data) {
    updateStateBadges(data.to);

    const tbody = document.getElementById('history-tbody');
    if (!tbody) return;

    const tr = document.createElement('tr');
    const conf = data.confidence || 0;
    const confPct = Math.round(conf * 100);
    const fromState = data.from || 'unknown';
    const toState = data.to || 'unknown';

    // Time
    const tdTime = document.createElement('td');
    tdTime.className = 'mono';
    tdTime.textContent = data.timestamp != null ? data.timestamp.toFixed(2) + 's' : '--';
    tr.appendChild(tdTime);

    // From
    const tdFrom = document.createElement('td');
    const fromBadge = document.createElement('span');
    fromBadge.className = 'state-badge state-' + fromState;
    fromBadge.textContent = fromState.toUpperCase();
    tdFrom.appendChild(fromBadge);
    tr.appendChild(tdFrom);

    // To
    const tdTo = document.createElement('td');
    const toBadge = document.createElement('span');
    toBadge.className = 'state-badge state-' + toState;
    toBadge.textContent = toState.toUpperCase();
    tdTo.appendChild(toBadge);
    tr.appendChild(tdTo);

    // Confidence
    const tdConf = document.createElement('td');
    const confBar = document.createElement('div');
    confBar.className = 'confidence-bar';
    const confFill = document.createElement('div');
    confFill.className = 'confidence-fill';
    confFill.style.width = confPct + '%';
    confBar.appendChild(confFill);
    const confText = document.createElement('span');
    confText.className = 'confidence-text mono';
    confText.textContent = confPct + '%';
    confBar.appendChild(confText);
    tdConf.appendChild(confBar);
    tr.appendChild(tdConf);

    // Trigger
    const tdTrigger = document.createElement('td');
    tdTrigger.className = 'mono';
    tdTrigger.textContent = JSON.stringify(data.signals || {});
    tr.appendChild(tdTrigger);

    // Duration
    const tdDuration = document.createElement('td');
    tdDuration.className = 'mono';
    tdDuration.textContent = data.duration != null ? data.duration.toFixed(1) + 's' : '\u2014';
    tr.appendChild(tdDuration);

    tbody.insertBefore(tr, tbody.firstChild);
  }

  // ----------------------------------------------------------------
  // uPlot Chart (dashboard page)
  // ----------------------------------------------------------------

  const chartEl = document.getElementById('score-chart');
  let uplotChart = null;
  let chartData = [[], []];

  if (chartEl && typeof uPlot !== 'undefined') {
    // Delay chart init slightly to ensure layout is computed
    setTimeout(() => {
      initChart();
      fetchScoreHistory();
      setInterval(fetchScoreHistory, 5000);
    }, 100);
  }

  function initChart() {
    const width = chartEl.clientWidth || 600;
    const height = 300;

    const opts = {
      width: width,
      height: height,
      cursor: { show: true },
      select: { show: false },
      scales: {
        x: { time: false },
        y: { range: [SCORE_MIN, SCORE_MAX] },
      },
      axes: [
        {
          stroke: '#8b8fa3',
          grid: { stroke: 'rgba(42,45,58,0.8)', width: 1 },
          ticks: { stroke: '#2a2d3a', width: 1 },
          font: '11px monospace',
          values: (u, vals) => vals.map(v => formatTimestamp(v)),
        },
        {
          stroke: '#8b8fa3',
          grid: { stroke: 'rgba(42,45,58,0.8)', width: 1 },
          ticks: { stroke: '#2a2d3a', width: 1 },
          font: '11px monospace',
        },
      ],
      series: [
        {},
        {
          label: 'Score',
          stroke: '#3b82f6',
          width: 2,
          fill: 'rgba(59,130,246,0.1)',
        },
      ],
      hooks: {
        draw: [
          (u) => {
            const ctx = u.ctx;
            const yComm = u.valToPos(THRESH_COMMERCIAL, 'y');
            const yProg = u.valToPos(THRESH_PROGRAM, 'y');
            const left = u.bbox.left;
            const right = left + u.bbox.width;

            ctx.save();
            ctx.setLineDash([6, 4]);
            ctx.lineWidth = 1;

            // Commercial threshold line
            ctx.strokeStyle = 'rgba(239,68,68,0.5)';
            ctx.beginPath();
            ctx.moveTo(left, yComm);
            ctx.lineTo(right, yComm);
            ctx.stroke();

            // Program threshold line
            ctx.strokeStyle = 'rgba(34,197,94,0.5)';
            ctx.beginPath();
            ctx.moveTo(left, yProg);
            ctx.lineTo(right, yProg);
            ctx.stroke();

            // Zero line
            const yZero = u.valToPos(0, 'y');
            ctx.strokeStyle = 'rgba(255,255,255,0.15)';
            ctx.setLineDash([2, 4]);
            ctx.beginPath();
            ctx.moveTo(left, yZero);
            ctx.lineTo(right, yZero);
            ctx.stroke();

            ctx.restore();
          },
        ],
      },
    };

    try {
      uplotChart = new uPlot(opts, chartData, chartEl);

      const ro = new ResizeObserver(() => {
        if (uplotChart && chartEl.clientWidth > 0) {
          uplotChart.setSize({ width: chartEl.clientWidth, height: 300 });
        }
      });
      ro.observe(chartEl);
    } catch (e) {
      console.error('uPlot init failed:', e);
    }
  }

  function fetchScoreHistory() {
    fetch('/api/score-history')
      .then(r => r.json())
      .then(data => {
        if (!Array.isArray(data) || data.length === 0) return;
        const timestamps = [];
        const scores = [];
        data.forEach(pt => {
          timestamps.push(pt[0]);
          scores.push(pt[1]);
        });
        chartData = [timestamps, scores];
        if (uplotChart) {
          uplotChart.setData(chartData);
        }
        // Update entry count indicator
        const countEl = document.getElementById('chart-entry-count');
        if (countEl) {
          countEl.textContent = data.length + ' data points';
        }
      })
      .catch(() => {});
  }

  // ----------------------------------------------------------------
  // Config form
  // ----------------------------------------------------------------

  const saveBtn = document.getElementById('config-save-btn');
  const resetBtn = document.getElementById('config-reset-btn');
  const feedback = document.getElementById('config-feedback');

  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      const form = document.getElementById('config-form');
      if (!form) return;

      const config = {};
      const inputs = form.querySelectorAll('input, select');

      inputs.forEach(input => {
        const name = input.name;
        if (!name) return;

        let value;
        if (input.type === 'number') {
          value = input.value === '' ? null : parseFloat(input.value);
        } else if (input.tagName === 'SELECT' &&
                   (input.value === 'true' || input.value === 'false')) {
          value = input.value === 'true';
        } else {
          value = input.value || null;
        }

        const parts = name.split('.');
        let obj = config;
        for (let i = 0; i < parts.length - 1; i++) {
          if (!obj[parts[i]]) obj[parts[i]] = {};
          obj = obj[parts[i]];
        }
        obj[parts[parts.length - 1]] = value;
      });

      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(() => {
          showFeedback('Configuration saved', 'success');
        })
        .catch(err => {
          showFeedback('Save failed: ' + err.message, 'error');
        });
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (!confirm('Reset all settings to defaults? This cannot be undone.')) return;
      fetch('/api/config', { method: 'DELETE' })
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(() => {
          showFeedback('Reset to defaults. Reloading\u2026', 'success');
          setTimeout(() => location.reload(), 1000);
        })
        .catch(err => {
          showFeedback('Reset failed: ' + err.message, 'error');
        });
    });
  }

  function showFeedback(msg, type) {
    if (!feedback) return;
    feedback.textContent = msg;
    feedback.className = 'config-feedback ' + type;
    setTimeout(() => {
      feedback.textContent = '';
      feedback.className = 'config-feedback';
    }, 4000);
  }

  // ----------------------------------------------------------------
  // System page — HTMX afterSwap
  // ----------------------------------------------------------------

  function fetchSystemInfo() {
    const systemCards = document.getElementById('system-cards');
    if (!systemCards) return;

    fetch('/api/system')
      .then(r => r.json())
      .then(data => {
        // CPU Temperature
        const tempValue = document.getElementById('temp-value');
        if (tempValue && data.cpu_temp_c != null) {
          tempValue.textContent = data.cpu_temp_c.toFixed(1);
          // SVG gauge (system page only)
          const tempArc = document.getElementById('temp-arc');
          if (tempArc) {
            const pct = Math.min(data.cpu_temp_c / 100, 1);
            const circumference = 2 * Math.PI * 52;
            tempArc.setAttribute('stroke-dasharray',
              (pct * circumference) + ' ' + circumference);
            tempArc.classList.remove('temp-ok', 'temp-warm', 'temp-hot');
            if (data.cpu_temp_c >= 75) tempArc.classList.add('temp-hot');
            else if (data.cpu_temp_c >= 60) tempArc.classList.add('temp-warm');
            else tempArc.classList.add('temp-ok');
          }
        }

        // Memory
        const memBar = document.getElementById('mem-bar');
        const memUsed = document.getElementById('mem-used');
        const memTotal = document.getElementById('mem-total');
        const memPctEl = document.getElementById('mem-pct');
        if (data.mem_total_mb != null) {
          if (memUsed) memUsed.textContent = data.mem_used_mb.toFixed(0);
          if (memTotal) memTotal.textContent = data.mem_total_mb.toFixed(0);
          if (memPctEl) memPctEl.textContent = data.mem_pct.toFixed(1) + '%';
          if (memBar) memBar.style.width = data.mem_pct.toFixed(1) + '%';
        }

        // Load Average
        if (data.load_avg) {
          const load1 = document.getElementById('load-1');
          const load5 = document.getElementById('load-5');
          const load15 = document.getElementById('load-15');
          if (load1) load1.textContent = data.load_avg[0].toFixed(2);
          if (load5) load5.textContent = data.load_avg[1].toFixed(2);
          if (load15) load15.textContent = data.load_avg[2].toFixed(2);
        }

        // System Uptime
        const sysUptime = document.getElementById('sys-uptime');
        if (sysUptime && data.system_uptime != null) {
          sysUptime.textContent = formatUptime(data.system_uptime);
        }

        // Disk
        const diskUsed = document.getElementById('disk-used');
        const diskTotal = document.getElementById('disk-total');
        if (diskUsed && data.disk_used_gb != null) {
          diskUsed.textContent = data.disk_used_gb + ' GB';
        }
        if (diskTotal && data.disk_total_gb != null) {
          diskTotal.textContent = data.disk_total_gb;
        }

        // Whisper status
        updateWhisperStatus(data);
      })
      .catch(() => {});
  }

  // Poll system info if system cards are visible (dashboard or system page)
  if (document.getElementById('system-cards')) {
    fetchSystemInfo();
    setInterval(fetchSystemInfo, 5000);
  }

  // ----------------------------------------------------------------
  // Whisper / Transcript status
  // ----------------------------------------------------------------

  function updateWhisperStatus(data) {
    const dot = document.getElementById('whisper-dot');
    const label = document.getElementById('whisper-label');
    const text = document.getElementById('whisper-text');
    if (!dot) return;

    if (data.whisper_running) {
      dot.className = 'whisper-dot whisper-dot-active';
      if (label) label.textContent = 'Whisper';
    } else if (data.whisper_enabled === false) {
      dot.className = 'whisper-dot whisper-dot-inactive';
      if (label) label.textContent = 'Whisper';
    } else {
      dot.className = 'whisper-dot whisper-dot-inactive';
      if (label) label.textContent = 'Whisper';
    }

    // Update transcript output on dashboard
    const output = document.getElementById('transcript-output');
    if (output && data.whisper_text) {
      appendTranscript(data.whisper_text);
    }
  }

  let _lastTranscriptText = '';
  function appendTranscript(text) {
    if (!text || text === _lastTranscriptText) return;
    _lastTranscriptText = text;
    const output = document.getElementById('transcript-output');
    if (!output) return;

    // Clear placeholder on first real text
    if (output.querySelector('.text-dim')) {
      output.innerHTML = '';
    }

    const span = document.createElement('span');
    span.className = 'transcript-chunk';
    span.textContent = text + ' ';
    output.appendChild(span);

    // Auto-scroll to bottom
    output.scrollTop = output.scrollHeight;
  }

  // ----------------------------------------------------------------
  // Utility
  // ----------------------------------------------------------------

  function formatTimestamp(seconds) {
    if (seconds == null || isNaN(seconds)) return '--';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return h + ':' + pad2(m) + ':' + pad2(sec);
    return m + ':' + pad2(sec);
  }

  function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return '--:--:--';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return pad2(h) + ':' + pad2(m) + ':' + pad2(sec);
  }

  function pad2(n) {
    return n < 10 ? '0' + n : '' + n;
  }

  // ----------------------------------------------------------------
  // Device Detection
  // ----------------------------------------------------------------

  const detectBtn = document.getElementById('detect-devices-btn');
  const detectFeedback = document.getElementById('detect-feedback');

  function fetchAndPopulateDevices() {
    return fetch('/api/devices')
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(data => {
        populateDeviceDropdown('capture-video_device', data.video);
        populateDeviceDropdown('capture-audio_device', data.audio);
        return data;
      });
  }

  // Auto-detect devices on page load
  fetchAndPopulateDevices().catch(() => {});

  if (detectBtn) {
    detectBtn.addEventListener('click', () => {
      detectBtn.disabled = true;
      detectBtn.textContent = 'Detecting\u2026';
      if (detectFeedback) {
        detectFeedback.textContent = '';
        detectFeedback.className = 'config-feedback';
      }

      fetchAndPopulateDevices()
        .then(data => {
          const total = (data.video || []).length + (data.audio || []).length;
          if (detectFeedback) {
            detectFeedback.textContent = total > 0
              ? 'Found ' + (data.video || []).length + ' video, ' + (data.audio || []).length + ' audio device(s)'
              : 'No devices found';
            detectFeedback.className = 'config-feedback ' + (total > 0 ? 'success' : 'error');
          }
        })
        .catch(err => {
          if (detectFeedback) {
            detectFeedback.textContent = 'Detection failed: ' + err.message;
            detectFeedback.className = 'config-feedback error';
          }
        })
        .finally(() => {
          detectBtn.disabled = false;
          detectBtn.textContent = 'Detect Devices';
        });
    });
  }

  function populateDeviceDropdown(inputId, devices) {
    const select = document.getElementById(inputId + '-select');
    const input = document.getElementById(inputId);
    if (!select || !input) return;

    // Clear existing options except placeholder
    while (select.options.length > 1) {
      select.remove(1);
    }

    (devices || []).forEach(dev => {
      const opt = document.createElement('option');
      opt.value = dev.path;
      opt.textContent = dev.path + ' \u2014 ' + dev.name;
      select.appendChild(opt);
    });

    // Auto-select if current value matches a discovered device
    const currentVal = input.value;
    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === currentVal) {
        select.selectedIndex = i;
        break;
      }
    }

    // When dropdown changes, update the text input
    select.onchange = () => {
      if (select.value) {
        input.value = select.value;
      }
    };
  }
});
