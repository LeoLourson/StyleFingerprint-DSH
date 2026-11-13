/**
 * Browser half of `dsh-style-fingerprint`: one page inside the Plugins settings
 * section (`settings.plugins.tab`), id `style-fingerprint`.
 *
 * The page talks to its host half over the same-origin route the host
 * registers (`/plugins/style-fingerprint`), so a settings change applies
 * without a restart. Controls are the host primitives plus local markup;
 * every colour is a `--dsw-alias-*` token (or a token from this plugin's own
 * override layer, registered through `ctx.get('theme')` and holding such a token).
 */
window.__ModuleLoader__.load({
  id: 'dsh-style-fingerprint',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const { useCallback, useEffect, useMemo, useRef, useState } = React;

    // Host primitives are the only shared control library. Keep the import
    // optional: an older page build without the module still renders the
    // local fallbacks below instead of blanking the slot entry.
    let primitives = {};
    try {
      primitives = require('@deepseek-ai/dsh-client-ui-primitives') || {};
    } catch (error) {
      primitives = {};
    }
    const PrimButton = typeof primitives.Button === 'function' ? primitives.Button : null;
    const PrimCheckbox = typeof primitives.Checkbox === 'function' ? primitives.Checkbox : null;

    const GATEWAY = '/plugins/style-fingerprint';
    const NS = 'style-fingerprint';
    const PROFILE_IDS = ['chat_answer', 'technical_doc', 'business_letter', 'prose_essay', 'math_paper'];
    const LAYER_IDS = ['central', 'language', 'composition', 'author', 'classification', 'corpus', 'math'];

    // #region copy

    const STRINGS = {
      ru: {
        tab: 'Стилевой отпечаток',
        nav: 'Разделы страницы',
        tabSettings: 'Настройки',
        tabThresholds: 'Пороги',
        tabCheck: 'Проверка',
        title: 'Стилевой отпечаток',
        lead: 'Закон и шаблоны перед документом, проверка готового текста с местами и правками. Настройки действуют сразу, без перезапуска.',
        defaultProfile: 'Профиль по умолчанию',
        defaultProfileHint: 'Его берёт вызов, который не назвал профиль: проверка без профиля и подстановки в разделах «Проверка» и «Пороги».',
        allowedProfiles: 'Разрешённые профили',
        allowedProfilesHint: 'Вызов называет один профиль; здесь перечислены те, что он может назвать. Выключенный профиль вызов не примет.',
        layers: 'Слои закона',
        layersHint: 'Слой выключен — его правила и файлы не попадают в brief и в проверку.',
        strict: 'Строгий режим',
        strictHint: 'Точные цели без запаса; промах метрики считается ошибкой.',
        margin: 'Запас порогов',
        marginHint: 'Доля, на которую расширяются целевые интервалы: 0,15 — это ±15 %. В строгом режиме не применяется.',
        author: 'Автор образцов',
        authorHint: 'Чьи цитаты показывает brief и поиск по корпусу. Числа по образцам не выдаются за норму.',
        authorTolstoy: 'Толстой',
        authorDostoevsky: 'Достоевский',
        authorBoth: 'Оба',
        gateWords: 'Порог проверки, слов',
        gateWordsHint: 'Короче порога метрики считаются, но ворот не закрывают: правила проверяются всегда.',
        saveIdle: 'Изменения сохраняются сразу',
        savePending: 'Сохраняю…',
        saveSaved: 'Сохранено',
        saveFailed: 'Не сохранено',
        settingsSource: 'Источник настроек',
        engine: 'Движок метрик',
        engineReady: 'доступен, версия {version}',
        engineDown: 'недоступен',
        engineReason: 'Причина',
        python: 'Python',
        corpus: 'Корпус',
        corpusLiterary: 'литературный',
        corpusMath: 'математический',
        corpusMissing: 'не собран',
        corpusBuilt: 'собран {built}',
        corpusAuthor: '{words} слов, {works} работ, цель {target}',
        corpusExcerpts: 'цитат в индексе: {count}',
        corpusNone: 'нет файла манифеста',
        thresholds: 'Таблица порогов',
        thresholdsHint: 'Интервалы из data/targets.json. Своё значение заменяет цель профиля и помечается.',
        thresholdProfile: 'Профиль',
        metric: 'Метрика',
        unit: 'Единица',
        min: 'От',
        max: 'До',
        sourceRule: 'правило',
        sourceCorpus: 'корпус',
        sourceAuthor: 'автор',
        reset: 'Снять',
        overridden: 'своё',
        noTargets: 'Для этого профиля целей нет: ворота метрик не проверяются.',
        catalogueMissing: 'Каталог метрик не собран',
        checkTitle: 'Проверка вставленного текста',
        checkHint: 'Вставьте текст и нажмите «Проверить». Ошибки исправляются до отправки; предупреждения — по решению.',
        checkPlaceholder: 'Вставьте текст…',
        checkRun: 'Проверить',
        checkBusy: 'Проверяю…',
        verdictPass: 'чисто',
        verdictWarn: 'предупреждения',
        verdictFail: 'ошибки',
        score: 'Счёт',
        counts: 'слов {words}, предложений {sentences}, абзацев {paragraphs}',
        violations: 'Нарушения',
        noViolations: 'Нарушений нет.',
        places: 'Места',
        next: 'Что сделать',
        notes: 'Заметки',
        severityError: 'ошибка',
        severityWarning: 'предупреждение',
        engineOff: 'Движок недоступен: правила проверены, метрики нет.',
        pageDown: 'Хост-канал не поднят: страница показывает значения из настроек, но сохранить их не может.',
        loading: 'Читаю настройки…',
        loadFailed: 'Настройки не прочитаны',
        empty: 'Пусто',
        allProfilesOff: 'Все профили выключены',
      },
      en: {
        tab: 'Style fingerprint',
        nav: 'Page sections',
        tabSettings: 'Settings',
        tabThresholds: 'Thresholds',
        tabCheck: 'Check',
        title: 'Style fingerprint',
        lead: 'Law and templates before a document, a check of the finished text with places and fixes. Settings apply immediately, without a restart.',
        defaultProfile: 'Default profile',
        defaultProfileHint: 'A call that names no profile takes it: the check without a profile and the presets in the Check and Thresholds sections.',
        allowedProfiles: 'Allowed profiles',
        allowedProfilesHint: 'A call names one profile; these are the ones it may name. A profile that is off is refused.',
        layers: 'Law layers',
        layersHint: 'A layer that is off keeps its rules and files out of brief and out of the check.',
        strict: 'Strict mode',
        strictHint: 'Exact targets without margin; a metric miss counts as an error.',
        margin: 'Threshold margin',
        marginHint: 'Share by which target intervals widen: 0.15 means ±15 %. Strict mode ignores it.',
        author: 'Sample author',
        authorHint: 'Whose quotes brief and the corpus search show. Numbers from samples are not a norm.',
        authorTolstoy: 'Tolstoy',
        authorDostoevsky: 'Dostoevsky',
        authorBoth: 'Both',
        gateWords: 'Check threshold, words',
        gateWordsHint: 'Below the threshold metrics are measured but gate nothing; rules are always checked.',
        saveIdle: 'Changes are saved as you type',
        savePending: 'Saving…',
        saveSaved: 'Saved',
        saveFailed: 'Not saved',
        settingsSource: 'Settings source',
        engine: 'Metric engine',
        engineReady: 'available, version {version}',
        engineDown: 'unavailable',
        engineReason: 'Reason',
        python: 'Python',
        corpus: 'Corpus',
        corpusLiterary: 'literary',
        corpusMath: 'mathematical',
        corpusMissing: 'not built',
        corpusBuilt: 'built {built}',
        corpusAuthor: '{words} words, {works} works, target {target}',
        corpusExcerpts: 'excerpts in the index: {count}',
        corpusNone: 'no manifest file',
        thresholds: 'Threshold table',
        thresholdsHint: 'Intervals from data/targets.json. A value of your own replaces the profile target and is marked.',
        thresholdProfile: 'Profile',
        metric: 'Metric',
        unit: 'Unit',
        min: 'Min',
        max: 'Max',
        sourceRule: 'rule',
        sourceCorpus: 'corpus',
        sourceAuthor: 'author',
        reset: 'Reset',
        overridden: 'own',
        noTargets: 'This profile has no targets: metric gates are not checked.',
        catalogueMissing: 'The metric catalogue is not built',
        checkTitle: 'Check a pasted text',
        checkHint: 'Paste a text and press Check. Errors are fixed before sending; warnings are a decision.',
        checkPlaceholder: 'Paste the text…',
        checkRun: 'Check',
        checkBusy: 'Checking…',
        verdictPass: 'clean',
        verdictWarn: 'warnings',
        verdictFail: 'errors',
        score: 'Score',
        counts: '{words} words, {sentences} sentences, {paragraphs} paragraphs',
        violations: 'Violations',
        noViolations: 'No violations.',
        places: 'Places',
        next: 'What to do',
        notes: 'Notes',
        severityError: 'error',
        severityWarning: 'warning',
        engineOff: 'The engine is down: rules were checked, metrics were not.',
        pageDown: 'The host channel is down: the page shows stored values but cannot save them.',
        loading: 'Reading settings…',
        loadFailed: 'Settings were not read',
        empty: 'Empty',
        allProfilesOff: 'All profiles are off',
      },
    };

    let translate = null;
    let localeCtx = null;

    function t(key, params) {
      let template = null;
      if (typeof translate === 'function') {
        try {
          const value = translate(key);
          if (typeof value === 'string' && value !== '' && value !== key) template = value;
        } catch (error) {
          template = null;
        }
      }
      if (template === null) template = STRINGS.en[key] !== undefined ? STRINGS.en[key] : key;
      if (!params) return template;
      return template.replace(/\{(\w+)\}/gu, (match, name) => (params[name] !== undefined ? String(params[name]) : match));
    }

    function useLocaleTick() {
      const [, force] = useState(0);
      useEffect(() => {
        if (!localeCtx || !localeCtx.locale || typeof localeCtx.locale.subscribe !== 'function') return undefined;
        try {
          return localeCtx.locale.subscribe(() => force((value) => value + 1));
        } catch (error) {
          return undefined;
        }
      }, []);
    }

    // #endregion
    // #region data

    function errorText(error) {
      if (error instanceof Error) return error.message;
      return String(error === undefined || error === null ? 'unknown error' : error);
    }

    function mergeSettings(current, patch) {
      const next = { ...(current || {}) };
      for (const [key, value] of Object.entries(patch || {})) {
        if (value && typeof value === 'object' && !Array.isArray(value)) next[key] = { ...(next[key] || {}), ...value };
        else next[key] = value;
      }
      return next;
    }

    async function readJson(response) {
      let body = null;
      try {
        body = await response.json();
      } catch (error) {
        body = null;
      }
      if (!response.ok) {
        const reason = body && body.error ? String(body.error) : `HTTP ${response.status}`;
        throw new Error(reason);
      }
      if (body && body.error) throw new Error(String(body.error));
      return body || {};
    }

    // #endregion
    // #region small controls

    function Button(props) {
      const { variant, size, icon, children, className, ...rest } = props;
      if (PrimButton) return h(PrimButton, { variant, size, icon, className, ...rest }, children);
      return h('button', { type: 'button', className, ...rest }, children);
    }

    function Checkbox(props) {
      const { checked, onChange, label, className } = props;
      if (PrimCheckbox) return h(PrimCheckbox, { checked, onChange, label, className });
      return h('label', { className: `dsfp_check ${className || ''}`.trim() },
        h('input', { type: 'checkbox', checked, onChange: (event) => onChange(event.target.checked) }),
        h('span', null, label));
    }

    function Switch(props) {
      const { checked, onChange, label } = props;
      return h('button', {
        type: 'button',
        role: 'switch',
        'aria-checked': checked ? 'true' : 'false',
        'aria-label': label,
        className: `dsfp_switch ${checked ? 'dsfp_switchOn' : ''}`.trim(),
        onClick: () => onChange(!checked),
      }, h('span', { className: 'dsfp_knob' }));
    }

    function Badge(props) {
      const { tone, children } = props;
      return h('span', { className: `dsfp_badge dsfp_badge${tone}` }, children);
    }

    function Icon(props) {
      // One 16px artwork; paint follows currentColor, so no colour literal here.
      const { kind } = props;
      if (kind === 'check') {
        return h('svg', { viewBox: '0 0 16 16', width: 16, height: 16, 'aria-hidden': true, className: 'dsfp_icon' },
          h('path', { d: 'M3.5 8.5l3 3 6-7', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' }));
      }
      if (kind === 'alert') {
        return h('svg', { viewBox: '0 0 16 16', width: 16, height: 16, 'aria-hidden': true, className: 'dsfp_icon' },
          h('path', { d: 'M8 2.5l6 11H2z', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinejoin: 'round' }),
          h('path', { d: 'M8 6.2v3.6M8 11.6v.6', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round' }));
      }
      return h('svg', { viewBox: '0 0 16 16', width: 16, height: 16, 'aria-hidden': true, className: 'dsfp_icon' },
        h('path', { d: 'M4 12V6a4 4 0 018 0v6', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round' }));
    }

    function Field(props) {
      const { label, hint, children } = props;
      return h('div', { className: 'dsfp_field' },
        h('div', { className: 'dsfp_fieldText' },
          h('div', { className: 'dsfp_label' }, label),
          hint ? h('div', { className: 'dsfp_hint' }, hint) : null),
        h('div', { className: 'dsfp_control' }, children));
    }

    function Group(props) {
      const { title, hint, children } = props;
      return h('section', { className: 'dsfp_group' },
        h('div', { className: 'dsfp_groupHead' },
          h('div', { className: 'dsfp_groupTitle' }, title),
          hint ? h('div', { className: 'dsfp_hint' }, hint) : null),
        children);
    }

    // #endregion
    // #region page sections

    function SettingsTab(props) {
      const { values, statics, commit } = props;
      const profileRows = statics.profiles || [];
      const layerRows = statics.layers || [];
      const engine = statics.engine || {};
      const corpora = statics.corpora || { literary: statics.corpus || {}, math: {} };
      const engineText = engine.available
        ? t('engineReady', { version: engine.version || '—' })
        : t('engineDown');
      const authorLabel = (id) => (id === 'tolstoy' ? t('authorTolstoy') : id === 'dostoevsky' ? t('authorDostoevsky') : id);

      return h('div', { className: 'dsfp_tabBody' },
        h(Group, { title: t('defaultProfile'), hint: t('defaultProfileHint') },
          h(Field, {
            label: t('defaultProfile'),
            children: h('select', {
              className: 'dsfp_input dsfp_select',
              value: values.defaultProfile || 'chat_answer',
              onChange: (event) => commit({ defaultProfile: event.target.value }),
            }, profileRows.map((profile) => h('option', { key: profile.id, value: profile.id }, profile.title || profile.id))),
          })),
        h(Group, { title: t('allowedProfiles'), hint: t('allowedProfilesHint') },
          h('div', { className: 'dsfp_grid' },
            profileRows.map((profile) => h(Checkbox, {
              key: profile.id,
              checked: values.profiles ? values.profiles[profile.id] !== false : true,
              label: profile.title || profile.id,
              onChange: (checked) => commit({ profiles: { [profile.id]: checked } }),
            })))),
        h(Group, { title: t('layers'), hint: t('layersHint') },
          h('div', { className: 'dsfp_grid' },
            layerRows.map((layer) => h(Checkbox, {
              key: layer.id,
              checked: values.layers ? values.layers[layer.id] !== false : true,
              label: layer.id,
              onChange: (checked) => commit({ layers: { [layer.id]: checked } }),
            })))),
        h(Group, { title: t('strict') },
          h(Field, {
            label: t('strict'),
            hint: t('strictHint'),
            children: h(Switch, {
              checked: values.strict !== false,
              label: t('strict'),
              onChange: (checked) => commit({ strict: checked }),
            }),
          }),
          h(Field, {
            label: t('margin'),
            hint: t('marginHint'),
            children: h('input', {
              className: 'dsfp_input dsfp_num',
              type: 'number',
              min: 0,
              max: 1,
              step: 0.01,
              value: values.margin === undefined ? 0.15 : values.margin,
              onChange: (event) => commit({ margin: Number(event.target.value) }),
            }),
          }),
          h(Field, {
            label: t('author'),
            hint: t('authorHint'),
            children: h('select', {
              className: 'dsfp_input dsfp_select',
              value: values.author || 'both',
              onChange: (event) => commit({ author: event.target.value }),
            },
            h('option', { value: 'tolstoy' }, t('authorTolstoy')),
            h('option', { value: 'dostoevsky' }, t('authorDostoevsky')),
            h('option', { value: 'both' }, t('authorBoth'))),
          }),
          h(Field, {
            label: t('gateWords'),
            hint: t('gateWordsHint'),
            children: h('input', {
              className: 'dsfp_input dsfp_num',
              type: 'number',
              min: 0,
              step: 10,
              value: values.gate && values.gate.minWordsForCheck !== undefined ? values.gate.minWordsForCheck : 60,
              onChange: (event) => commit({ gate: { minWordsForCheck: Number(event.target.value) } }),
            }),
          })),
        h('div', { className: 'dsfp_status' },
          h('div', { className: 'dsfp_statusLine' },
            h(Icon, { kind: engine.available ? 'check' : 'alert' }),
            h('span', { className: 'dsfp_label' }, t('engine')),
            h('span', { className: 'dsfp_hint' }, engineText),
            engine.available ? null : h('span', { className: 'dsfp_hint' }, `${t('engineReason')}: ${engine.reason || '—'}`)),
          h('div', { className: 'dsfp_statusLine' },
            h('span', { className: 'dsfp_label' }, t('python')),
            h('span', { className: 'dsfp_hint' }, statics.settings && statics.python ? `${statics.python.command} (${statics.python.source})` : '—')),
          [
            ['literary', t('corpusLiterary')],
            ['math', t('corpusMath')],
          ].map(([kind, label]) => {
            const row = corpora[kind] || {};
            const authors = row.authors || {};
            return h('div', { className: 'dsfp_corpusRow', key: kind },
              h('div', { className: 'dsfp_statusLine' },
                h('span', { className: 'dsfp_label' }, `${t('corpus')}: ${label}`),
                row.available
                  ? h('span', { className: 'dsfp_hint' }, [
                    row.built ? t('corpusBuilt', { built: row.built }) : '',
                    ` · ${t('corpusExcerpts', { count: row.excerpts || 0 })}`,
                  ].filter(Boolean).join(''))
                  : h('span', { className: 'dsfp_hint' }, `${t('corpusMissing')}: ${row.error || t('corpusNone')}`)),
              row.available
                ? Object.entries(authors).map(([author, stats]) => h('div', { className: 'dsfp_statusLine', key: author },
                  h('span', { className: 'dsfp_label dsfp_author' }, authorLabel(author)),
                  h('span', { className: 'dsfp_hint' }, t('corpusAuthor', stats))))
                : null);
          }),
          h('div', { className: 'dsfp_statusLine' },
            h('span', { className: 'dsfp_label' }, t('settingsSource')),
            h('span', { className: 'dsfp_hint' }, statics.source || '—'))));
    }

    function ThresholdsTab(props) {
      const { statics, values, profile, setProfile, overrides, setOverride } = props;
      const targets = (statics.targets && statics.targets[profile]) || { margin: 0, metrics: {} };
      const catalogue = statics.catalogue || { metrics: [] };
      const byId = useMemo(() => {
        const map = new Map();
        for (const metric of catalogue.metrics || []) map.set(metric.id, metric);
        return map;
      }, [catalogue]);
      const rows = Object.entries(targets.metrics || {});

      return h('div', { className: 'dsfp_tabBody' },
        h(Field, {
          label: t('thresholdProfile'),
          hint: t('thresholdsHint'),
          children: h('select', {
            className: 'dsfp_input dsfp_select',
            value: profile,
            onChange: (event) => setProfile(event.target.value),
          }, (statics.profiles || []).map((row) => h('option', { key: row.id, value: row.id }, row.title || row.id))),
        }),
        catalogue.available === false
          ? h('div', { className: 'dsfp_notice' }, `${t('catalogueMissing')}: ${catalogue.error || ''}`)
          : null,
        rows.length === 0
          ? h('div', { className: 'dsfp_notice' }, t('noTargets'))
          : h('table', { className: 'dsfp_table' },
            h('thead', null,
              h('tr', null,
                h('th', { className: 'dsfp_th' }, t('metric')),
                h('th', { className: 'dsfp_th' }, t('unit')),
                h('th', { className: 'dsfp_th' }, t('min')),
                h('th', { className: 'dsfp_th' }, t('max')),
                h('th', { className: 'dsfp_th' }, t('settingsSource')),
                h('th', { className: 'dsfp_th' }, ''))),
            h('tbody', null,
              rows.map(([id, target]) => {
                const metric = byId.get(id) || {};
                const own = overrides[id];
                return h('tr', { key: id, className: own ? 'dsfp_trOwn' : '' },
                  h('td', { className: 'dsfp_td' },
                    h('div', null, metric.label || id),
                    h('div', { className: 'dsfp_hint' }, id)),
                  h('td', { className: 'dsfp_td dsfp_hint' }, metric.unit || ''),
                  h('td', { className: 'dsfp_td' }, h('input', {
                    className: 'dsfp_input dsfp_num',
                    type: 'number',
                    step: 'any',
                    value: own && Number.isFinite(own.min) ? own.min : target.min,
                    onChange: (event) => setOverride(id, Number(event.target.value), (own && own.max !== undefined ? own.max : target.max)),
                  })),
                  h('td', { className: 'dsfp_td' }, h('input', {
                    className: 'dsfp_input dsfp_num',
                    type: 'number',
                    step: 'any',
                    value: own && Number.isFinite(own.max) ? own.max : target.max,
                    onChange: (event) => setOverride(id, (own && own.min !== undefined ? own.min : target.min), Number(event.target.value)),
                  })),
                  h('td', { className: 'dsfp_td dsfp_hint' },
                    `${target.source === 'corpus' ? t('sourceCorpus') : target.source === 'author' ? t('sourceAuthor') : t('sourceRule')}${own ? ` · ${t('overridden')}` : ''}`),
                  h('td', { className: 'dsfp_td' },
                    own ? h(Button, {
                      variant: 'ghost',
                      size: 'sm',
                      onClick: () => setOverride(id, null, null),
                    }, t('reset')) : null));
              }))),
        h('div', { className: 'dsfp_hint' }, `margin ${targets.margin === undefined ? 0 : targets.margin}, ${t('strict')}: ${values.strict ? 'on' : 'off'}`));
    }

    function ViolationRow(props) {
      const { violation } = props;
      const tone = violation.severity === 'error' ? 'Fail' : 'Warn';
      return h('div', { className: 'dsfp_violation' },
        h('div', { className: 'dsfp_rowHead' },
          h(Icon, { kind: violation.severity === 'error' ? 'alert' : 'finger' }),
          h(Badge, { tone }, violation.severity === 'error' ? t('severityError') : t('severityWarning')),
          h('span', { className: 'dsfp_label' }, violation.title || violation.rule),
          violation.ref ? h('span', { className: 'dsfp_hint' }, violation.ref) : null,
          Number.isFinite(violation.value) ? h('span', { className: 'dsfp_hint' }, `${violation.value}${violation.units === 'per1000' ? ' / 1000' : ''}`) : null),
        violation.message ? h('div', { className: 'dsfp_hint' }, violation.message) : null,
        Array.isArray(violation.places) && violation.places.length > 0
          ? h('div', { className: 'dsfp_places' },
            h('div', { className: 'dsfp_hint' }, t('places')),
            violation.places.map((place, index) => h('p', { className: 'dsfp_place', key: index }, place)))
          : null,
        violation.fix ? h('div', { className: 'dsfp_fix' }, violation.fix) : null);
    }

    function CheckTab(props) {
      const { statics, pageOpen, text, setText, profile, setProfile, result, setResult, busy, setBusy } = props;

      const run = useCallback(async () => {
        if (busy) return;
        setBusy(true);
        try {
          const response = await fetch(`${GATEWAY}/check`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ text, profile, strict: statics.values ? statics.values.strict : undefined }),
          });
          const body = await readJson(response);
          setResult(body);
        } catch (error) {
          setResult({ error: errorText(error) });
        } finally {
          setBusy(false);
        }
      }, [busy, text, profile, statics.values, setBusy, setResult]);

      const verdict = result && !result.error ? String(result.verdict || '') : '';
      const tone = verdict === 'fail' ? 'Fail' : verdict === 'warn' ? 'Warn' : 'Pass';
      const violations = result && Array.isArray(result.violations) ? result.violations : [];
      const next = result && Array.isArray(result.next) ? result.next : [];
      const notes = result && Array.isArray(result.notes) ? result.notes : [];

      return h('div', { className: 'dsfp_tabBody' },
        h(Group, { title: t('checkTitle'), hint: t('checkHint') },
          h(Field, {
            label: t('thresholdProfile'),
            children: h('select', {
              className: 'dsfp_input dsfp_select',
              value: profile,
              onChange: (event) => setProfile(event.target.value),
            }, (statics.profiles || []).map((row) => h('option', { key: row.id, value: row.id }, row.title || row.id))),
          }),
          h('textarea', {
            className: 'dsfp_textarea',
            value: text,
            placeholder: t('checkPlaceholder'),
            onChange: (event) => setText(event.target.value),
          }),
          h('div', { className: 'dsfp_actions' },
            h(Button, {
              variant: 'primary',
              size: 'md',
              onClick: run,
              disabled: busy || !pageOpen || text.trim() === '',
            }, busy ? t('checkBusy') : t('checkRun')),
            result && !result.error
              ? h('div', { className: 'dsfp_verdictBox' },
                h(Badge, { tone }, verdict === 'fail' ? t('verdictFail') : verdict === 'warn' ? t('verdictWarn') : t('verdictPass')),
                h('span', { className: 'dsfp_hint' }, `${t('score')}: ${result.score}`),
                result.counts ? h('span', { className: 'dsfp_hint' }, t('counts', result.counts)) : null)
              : null)),
        pageOpen ? null : h('div', { className: 'dsfp_notice' }, t('pageDown')),
        result && result.error ? h('div', { className: 'dsfp_error' }, `${t('loadFailed')}: ${result.error}`) : null,
        result && !result.error && result.engine && !result.engine.available ? h('div', { className: 'dsfp_notice' }, t('engineOff')) : null,
        result && !result.error
          ? h(Group, { title: `${t('violations')}: ${violations.length}` },
            violations.length === 0
              ? h('div', { className: 'dsfp_hint' }, t('noViolations'))
              : violations.map((violation, index) => h(ViolationRow, { key: `${violation.rule}-${index}`, violation })))
          : null,
        result && !result.error && next.length > 0
          ? h(Group, { title: t('next') }, next.map((line, index) => h('div', { className: 'dsfp_hint', key: index }, line)))
          : null,
        result && !result.error && notes.length > 0
          ? h(Group, { title: t('notes') }, notes.map((line, index) => h('div', { className: 'dsfp_hint', key: index }, line)))
          : null);
    }

    // #endregion
    // #region page

    function StyleFingerprintPage() {
      useLocaleTick();
      const [tab, setTab] = useState('settings');
      const [loaded, setLoaded] = useState({ status: 'loading', data: null, error: null });
      const [values, setValues] = useState(null);
      const [save, setSave] = useState({ status: 'idle', error: null, at: null, problems: [] });
      const [thresholdProfile, setThresholdProfile] = useState('chat_answer');
      const [checkProfile, setCheckProfile] = useState('chat_answer');
      const [text, setText] = useState('');
      const [result, setResult] = useState(null);
      const [busy, setBusy] = useState(false);
      const timer = useRef(null);
      const alive = useRef(true);

      useEffect(() => {
        alive.current = true;
        (async () => {
          try {
            const response = await fetch(`${GATEWAY}/state`, { headers: { accept: 'application/json' } });
            const data = await readJson(response);
            if (!alive.current) return;
            setLoaded({ status: 'ready', data, error: null });
            setValues(data.settings || {});
            const profile = (data.settings && data.settings.defaultProfile) || 'chat_answer';
            setThresholdProfile(PROFILE_IDS.includes(profile) ? profile : 'chat_answer');
            setCheckProfile(PROFILE_IDS.includes(profile) ? profile : 'chat_answer');
          } catch (error) {
            if (alive.current) setLoaded({ status: 'failed', data: null, error: errorText(error) });
          }
        })();
        return () => {
          alive.current = false;
          if (timer.current) clearTimeout(timer.current);
        };
      }, []);

      const send = useCallback(async (patch) => {
        setSave({ status: 'pending', error: null, at: null, problems: [] });
        try {
          const response = await fetch(`${GATEWAY}/settings`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(patch),
          });
          const body = await readJson(response);
          if (!alive.current) return;
          setSave({ status: 'saved', error: null, at: body.changedAt || null, problems: body.problems || [] });
          if (body.settings) setValues((current) => ({ ...current, ...body.settings }));
        } catch (error) {
          if (!alive.current) return;
          setSave({ status: 'failed', error: errorText(error), at: null, problems: [] });
        }
      }, []);

      const commit = useCallback((patch) => {
        setValues((current) => mergeSettings(current, patch));
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => { void send(patch); }, 350);
      }, [send]);

      const setOverride = useCallback((id, min, max) => {
        const current = (values && values.metricTargets) || {};
        const next = { ...current };
        if (min === null) delete next[id];
        else next[id] = { min, max };
        commit({ metricTargets: next });
      }, [values, commit]);

      const statics = loaded.data || {};
      const overrides = (values && values.metricTargets) || {};
      const pageOpen = Boolean(statics.page && statics.page.available);

      if (loaded.status === 'loading') return h('div', { className: 'dsfp_root' }, h('div', { className: 'dsfp_hint' }, t('loading')));
      if (loaded.status === 'failed') {
        return h('div', { className: 'dsfp_root' },
          h('div', { className: 'dsfp_error' }, `${t('loadFailed')}: ${loaded.error}`),
          h('div', { className: 'dsfp_hint' }, t('pageDown')));
      }
      if (!values) return h('div', { className: 'dsfp_root' }, h('div', { className: 'dsfp_hint' }, t('loading')));

      const saveText = save.status === 'pending' ? t('savePending')
        : save.status === 'saved' ? `${t('saveSaved')}${save.at ? ` · ${save.at}` : ''}`
          : save.status === 'failed' ? `${t('saveFailed')}: ${save.error || ''}`
            : t('saveIdle');

      return h('div', { className: 'dsfp_root' },
        h('div', { className: 'dsfp_head' },
          h('div', { className: 'dsfp_title' }, t('title')),
          h('div', { className: 'dsfp_hint' }, t('lead')),
          h('div', { className: `dsfp_hint ${save.status === 'failed' ? 'dsfp_error' : ''}`.trim(), role: 'status' }, saveText),
          !pageOpen && loaded.status === 'ready' ? h('div', { className: 'dsfp_notice' }, t('pageDown')) : null),
        h('div', { className: 'dsfp_nav', role: 'tablist', 'aria-label': t('nav') },
          [['settings', t('tabSettings')], ['thresholds', t('tabThresholds')], ['check', t('tabCheck')]].map(([id, label]) => h('button', {
            key: id,
            type: 'button',
            role: 'tab',
            'aria-selected': tab === id ? 'true' : 'false',
            className: `dsfp_navBtn ${tab === id ? 'dsfp_navBtnOn' : ''}`.trim(),
            onClick: () => setTab(id),
          }, label))),
        tab === 'settings'
          ? h(SettingsTab, { values, statics, commit })
          : tab === 'thresholds'
            ? h(ThresholdsTab, { statics, values, profile: thresholdProfile, setProfile: setThresholdProfile, overrides, setOverride })
            : h(CheckTab, {
              statics: { ...statics, values },
              pageOpen,
              text,
              setText,
              profile: checkProfile,
              setProfile: setCheckProfile,
              result,
              setResult,
              busy,
              setBusy,
            }));
    }

    // #endregion
    // #region styles

    const CSS = [
      '.dsfp_root{display:flex;flex-direction:column;gap:16px;color:var(--dsw-alias-label-primary);font:inherit;font-size:13px;line-height:1.5}',
      '.dsfp_head{display:flex;flex-direction:column;gap:4px}',
      '.dsfp_title{font-size:15px;font-weight:600;letter-spacing:0.01em}',
      '.dsfp_nav{display:flex;align-items:center;gap:8px;min-height:36px;padding:7px 8px;border-bottom:1px solid var(--dsw-alias-border-l1)}',
      '.dsfp_navBtn{display:inline-flex;align-items:center;justify-content:center;height:24px;padding:0 12px;border:0;border-radius:var(--dsw-radius-md);background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;line-height:1;cursor:pointer}',
      '.dsfp_navBtn:hover{background:var(--dsw-alias-interactive-bg-hover)}',
      '.dsfp_navBtnOn{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}',
      '.dsfp_tabBody{display:flex;flex-direction:column;gap:16px}',
      '.dsfp_group{display:flex;flex-direction:column;gap:12px;padding:12px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-layer-1)}',
      '.dsfp_groupHead{display:flex;flex-direction:column;gap:4px}',
      '.dsfp_groupTitle{font-size:13px;font-weight:600}',
      '.dsfp_grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 16px}',
      '.dsfp_field{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:32px}',
      '.dsfp_fieldText{display:flex;flex-direction:column;gap:4px;min-width:0}',
      '.dsfp_label{font-size:13px}',
      '.dsfp_hint{color:var(--dsw-alias-label-secondary);font-size:12px}',
      '.dsfp_control{display:flex;align-items:center;gap:8px;flex:none}',
      '.dsfp_input{box-sizing:border-box;min-height:32px;padding:6px 8px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font:inherit;font-size:13px;line-height:1.4;outline:none}',
      '.dsfp_input:focus{border-color:var(--dsw-alias-brand-primary)}',
      '.dsfp_input:disabled{opacity:0.5}',
      '.dsfp_num{width:96px}',
      '.dsfp_select{min-width:160px}',
      '.dsfp_check{display:inline-flex;align-items:center;gap:8px;min-height:24px;color:var(--dsw-alias-label-primary);font-size:13px;cursor:pointer}',
      '.dsfp_switch{position:relative;box-sizing:border-box;width:36px;height:20px;padding:0;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-layer-2);cursor:pointer}',
      '.dsfp_switchOn{border-color:transparent;background:var(--dsw-alias-brand-primary)}',
      '.dsfp_knob{position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:var(--dsw-radius-md);background:var(--dsw-alias-label-primary-foreground);transition:transform 0.15s ease}',
      '.dsfp_switchOn .dsfp_knob{transform:translateX(16px)}',
      '.dsfp_badge{display:inline-flex;align-items:center;height:20px;padding:0 8px;border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-layer-2);font-size:11px;line-height:1}',
      '.dsfp_badgePass{color:var(--dsw-style-fp-pass)}',
      '.dsfp_badgeWarn{color:var(--dsw-style-fp-warn)}',
      '.dsfp_badgeFail{color:var(--dsw-style-fp-fail)}',
      '.dsfp_icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;flex:none;color:currentColor}',
      '.dsfp_status{display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-layer-1)}',
      '.dsfp_statusLine{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-height:24px}',
      '.dsfp_corpusRow{display:flex;flex-direction:column;gap:4px}',
      '.dsfp_author{color:var(--dsw-alias-label-secondary)}',
      '.dsfp_notice{padding:8px 12px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);font-size:12px}',
      '.dsfp_error{color:var(--dsw-alias-state-error-primary);font-size:12px}',
      '.dsfp_table{width:100%;border-collapse:collapse}',
      '.dsfp_th{padding:8px;border-bottom:1px solid var(--dsw-alias-border-l2);color:var(--dsw-alias-label-secondary);font-size:11px;font-weight:600;text-align:left}',
      '.dsfp_td{padding:8px;border-bottom:1px solid var(--dsw-alias-border-l1);font-size:12px;vertical-align:middle}',
      '.dsfp_trOwn .dsfp_td{background:var(--dsw-alias-bg-layer-2)}',
      '.dsfp_textarea{box-sizing:border-box;width:100%;min-height:160px;padding:8px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font:inherit;font-size:13px;line-height:1.5;resize:vertical;outline:none}',
      '.dsfp_textarea:focus{border-color:var(--dsw-alias-brand-primary)}',
      '.dsfp_actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}',
      '.dsfp_verdictBox{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
      '.dsfp_violation{display:flex;flex-direction:column;gap:4px;padding:8px;border:1px solid var(--dsw-alias-border-l1);border-radius:var(--dsw-radius-md);background:var(--dsw-alias-bg-base)}',
      '.dsfp_rowHead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-height:24px}',
      '.dsfp_places{display:flex;flex-direction:column;gap:4px}',
      '.dsfp_place{margin:0;color:var(--dsw-alias-label-secondary);font-family:var(--dsw-font-mono,monospace);font-size:12px;word-break:break-word}',
      '.dsfp_fix{color:var(--dsw-alias-label-primary);font-size:12px}',
    ].join('\n');

    // #endregion

    function apply(ctx) {
      localeCtx = ctx;
      try {
        ctx.locale.register(NS, 'ru', STRINGS.ru);
        ctx.locale.register(NS, 'en', STRINGS.en);
      } catch (error) {
        // a duplicate registration on reload keeps the live dictionaries
      }
      try {
        translate = ctx.locale.bind(NS);
      } catch (error) {
        translate = null;
      }

      // Own semantic colours, each one an alias token in both palettes: the
      // override layer never introduces a literal colour and stays legible
      // when the user switches the scheme.
      ctx.effect(() => {
        const theme = ctx.get('theme');
        if (!theme || typeof theme.overrideTokens !== 'function') return () => {};
        return theme.overrideTokens('dsh-style-fingerprint', {
          '--dsw-style-fp-pass': {
            light: 'var(--dsw-alias-state-success-primary)',
            dark: 'var(--dsw-alias-state-success-primary)',
          },
          '--dsw-style-fp-warn': {
            light: 'var(--dsw-alias-state-warn-primary)',
            dark: 'var(--dsw-alias-state-warn-primary)',
          },
          '--dsw-style-fp-fail': {
            light: 'var(--dsw-alias-state-error-primary)',
            dark: 'var(--dsw-alias-state-error-primary)',
          },
        });
      }, 'style-fingerprint: tokens');

      ctx.effect(() => {
        const style = document.createElement('style');
        style.setAttribute('data-dsh-style-fingerprint', '');
        style.textContent = CSS;
        document.head.append(style);
        return () => {
          style.remove();
        };
      }, 'style-fingerprint: styles');

      // `settings.plugins.tab` is declared by the Plugins section; inject waits
      // for that declaration instead of registering into an undeclared slot.
      ctx.slots.inject('settings.plugins.tab', () => ctx.slots.register({
        name: 'settings.plugins.tab',
        id: 'style-fingerprint',
        order: 40,
        label: () => t('tab'),
      }, StyleFingerprintPage));
    }

    return {
      // `theme` is read through `ctx.get` behind a guard: the service stays
      // optional, so a profile without it keeps the page and skips only the
      // override layer. A property read (`ctx.theme`) would throw here, since
      // the name is not declared in `inject`.
      inject: ['slots', 'locale'],
      apply,
    };
  },
});
