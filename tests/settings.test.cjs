const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../src/Jellyfin.Plugin.VsrEverywhere/Configuration/configPage.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const presets = ['1920x1080', '2560x1440', '3840x2160', '5120x2880', '5760x3240', '7680x4320'];

// DOM/API stand-ins run the shipped page handlers without a Jellyfin server.
class Element {
    constructor(tag = 'div') {
        this.tag = tag; this.type = tag === 'select' ? 'select-one' : '';
        this.children = []; this.listeners = {}; this.dataset = {}; this.style = {};
        this._value = ''; this.checked = false;
    }
    get options() { return this.children.filter(child => child.tag === 'option'); }
    get value() { return this._value; }
    set value(value) {
        const text = String(value);
        this._value = this.tag !== 'select' || this.options.some(o => o.value === text) ? text : '';
    }
    append(...elements) { for (const el of elements) { el.parent = this; this.children.push(el); } }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    replaceChildren() { this.children = []; }
    querySelector(selector) {
        if (selector === 'option[data-saved]') return this.options.find(o => 'saved' in o.dataset) || null;
        return controls[selector.slice(1)] || null;
    }
    addEventListener(event, handler) { this.listeners[event] = handler; }
}
const controls = {};
for (const match of html.matchAll(/<(input|select|div|form|button|p|a)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const el = controls[match[3]] = new Element(match[1]);
    el.type = match[2].match(/\btype="([^"]+)"/)?.[1] || el.type;
    if (match[1] === 'select') {
        const body = html.slice(match.index + match[0].length).split('</select>')[0];
        for (const option of body.matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g)) {
            const child = new Element('option'); child.value = option[1]; child.textContent = option[2]; el.append(child);
        }
        el.value = el.options[0].value;
    }
}
for (const key of ['ProcessingResolution', 'StreamResolution']) {
    assert.equal(controls[key].tag, 'select');
    assert.deepEqual(controls[key].options.map(o => o.value), presets);
    for (const value of presets) { const [w, h] = value.split('x').map(Number); assert.equal(w * 9, h * 16); }
}
assert(!html.includes('All new devices default to disabled.'));
assert(!html.includes("Selection uses that device's Jellyfin registration ID"));
assert(!controls.ProcessingWidth && !controls.StreamWidth);
assert(html.includes('<title>VSR Everywhere</title>'));
assert(html.indexOf('id="RefreshVsrDevices"') < html.indexOf('id="EnableVsr"'));
assert(html.indexOf('id="EnableVsr"') < html.indexOf('id="EnableFrameInterpolation"'));
assert(html.indexOf('id="EnableFrameInterpolation"') < html.indexOf('id="ProcessingResolution"'));
assert(html.indexOf('id="StreamResolution"') < html.indexOf('<details>'));
assert(html.indexOf('<details>') < html.indexOf('id="NvencPreset"'));

let config = {
    Enabled: true, EnableVsr: true, ProcessAbove800p: false, EnableAllDevices: false, Backend: 'nvidia', EnabledDeviceIds: ['device-1'],
    ProcessingWidth: 5120, ProcessingHeight: 2880, StreamWidth: 3840, StreamHeight: 2160,
    PythonPath: 'python', FfmpegPath: 'ffmpeg', FfprobePath: 'ffprobe', CacheDirectory: 'cache',
    CacheMode: 'disk', MemoryBudgetMb: 0, EffectiveMemoryBudgetMb: 7168,
    CacheMaxGb: 10, CacheMinFreeGb: 5, CacheIdleMinutes: 10, EnableRollingCache: false,
    RollingRewindMinutes: 10, VideoMbps: 40, VsrQuality: 'HIGH', VsrStrength: 0.75,
    NvencPreset: 'p5', NvencTune: 'hq', NvencLookahead: 12, NvencSpatialAq: true,
    NvencTemporalAq: false, NvencCq: 0, SegmentType: 'mpegts', MaxSessions: 1,
    SessionMinutes: 180, FutureSetting: 'keep me',
    EnableAutoHdr: false, EnableFrameInterpolation: true
};
const pluginId = '7ca129d0-7454-41c7-aad6-55250b1a7b5b';
let updateReply = { currentVersion: '1.5.0', latestVersion: '1.5.0', canInstall: false, updateAvailable: false,
    restartRequired: false, message: 'Up to date.', releaseUrl: 'https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.5.0' };
let updateError = null, installError = null, updateWait = null, updateChecks = 0, updateInstalls = 0;
vm.runInNewContext(script, {
    document: { querySelector: () => controls.VsrEverywherePage, createElement: tag => new Element(tag) },
    ApiClient: {
        getPluginConfiguration: async id => { assert.equal(id, pluginId); return structuredClone(config); },
        updatePluginConfiguration: async (id, cfg) => { assert.equal(id, pluginId); config = structuredClone(cfg); return {}; },
        getUrl: name => name,
        getJSON: async url => {
            if (url === 'Devices') return { Items: [{ Id: 'device-1', Name: 'Living Room', AppName: 'Roku' }] };
            assert.equal(url, 'VsrEverywhere/Updates'); updateChecks++;
            if (updateWait) await updateWait;
            if (updateError) throw updateError;
            return structuredClone(updateReply);
        },
        ajax: async request => {
            assert.equal(request.type, 'POST'); assert.equal(request.url, 'VsrEverywhere/Updates');
            assert.equal(request.dataType, 'json'); updateInstalls++;
            if (installError) throw installError;
            return structuredClone(updateReply);
        }
    },
    Dashboard: { showLoadingMsg() {}, hideLoadingMsg() {}, processPluginConfigurationUpdateResult() {} },
    console: { error(...args) { throw new Error(args.join(' ')); } }
});
const load = () => controls.VsrEverywherePage.listeners.pageshow();
const save = () => controls.VsrEverywhereForm.listeners.submit({ preventDefault() {} });
(async () => {
    await load();
    assert.equal(controls.ProcessingResolution.value, '5120x2880');
    assert.equal(controls.StreamResolution.value, '3840x2160');
    const original = structuredClone(config);
    await save();
    assert.deepEqual(config, original, 'Opening and saving must retain the existing configuration');
    for (const enabled of [false, true]) {
        controls.EnableVsr.checked = enabled;
        controls.EnableFrameInterpolation.checked = enabled;
        controls.ProcessAbove800p.checked = enabled;
        await save(); await load();
        assert.equal(config.EnableFrameInterpolation, enabled);
        assert.equal(config.EnableVsr, enabled);
        assert.equal(controls.EnableFrameInterpolation.checked, enabled);
        assert.equal(config.ProcessAbove800p, enabled);
        assert.equal(controls.ProcessAbove800p.checked, enabled);
    }
    delete config.ProcessAbove800p;
    await load();
    assert.equal(controls.ProcessAbove800p.checked, false, 'Missing setting must default to 800p and below');
    await save();
    assert.equal(config.ProcessAbove800p, false);
    delete config.EnableVsr;
    await load();
    assert.equal(controls.EnableVsr.checked, true, 'Existing installations must keep VSR enabled');
    await save();
    assert.equal(config.EnableVsr, true);
    controls.EnableAutoHdr.checked = true;
    await save(); await load();
    assert.equal(config.EnableAutoHdr, true);
    assert.equal(controls.EnableAutoHdr.checked, true);
    assert.equal(controls.DiskCacheFields.hidden, false);
    assert.equal(controls.MemoryCacheFields.hidden, true);
    controls.CacheMode.value = 'memory';
    controls.CacheMode.listeners.change();
    assert.equal(controls.DiskCacheFields.hidden, true);
    assert.equal(controls.DiskRollingCache.hidden, true);
    assert.equal(controls.MemoryCacheFields.hidden, false);
    assert(controls.MemoryBudgetStatus.textContent.includes('7168 MB'));
    controls.MemoryBudgetMb.value = '512';
    controls.MemoryBudgetMb.listeners.input();
    assert(controls.MemoryBudgetStatus.textContent.includes('512 MB'));
    await save();
    assert.equal(config.CacheMode, 'memory');
    assert.equal(config.MemoryBudgetMb, 512);
    assert.equal(config.CacheDirectory, original.CacheDirectory);
    assert.equal(config.CacheMaxGb, original.CacheMaxGb);
    assert.equal(config.CacheMinFreeGb, original.CacheMinFreeGb);
    for (const processing of presets) for (const stream of presets) {
        controls.ProcessingResolution.value = processing; controls.StreamResolution.value = stream;
        await save();
        assert.deepEqual([config.ProcessingWidth, config.ProcessingHeight], processing.split('x').map(Number));
        assert.deepEqual([config.StreamWidth, config.StreamHeight], stream.split('x').map(Number));
        assert.deepEqual(config.EnabledDeviceIds, ['device-1']);
        assert.equal(config.FutureSetting, 'keep me');
        await load();
        assert.equal(controls.ProcessingResolution.value, processing);
        assert.equal(controls.StreamResolution.value, stream);
    }
    controls.EnableAllDevices.checked = true;
    controls.EnableAllDevices.listeners.change();
    assert(controls.VsrDevicesList.children[0].children[0].checked);
    assert(controls.VsrDevicesList.children[0].children[0].disabled);
    await save(); await load();
    assert.equal(config.EnableAllDevices, true);
    assert.deepEqual(config.EnabledDeviceIds, ['device-1']);
    controls.EnableAllDevices.checked = false;
    controls.EnableAllDevices.listeners.change();
    assert.equal(controls.VsrDevicesList.children[0].children[0].disabled, false);
    await save();
    assert.equal(config.EnableAllDevices, false);
    config.ProcessingWidth = 4800; config.ProcessingHeight = 2700;
    await load(); await load();
    assert.equal(controls.ProcessingResolution.options.length, presets.length + 1);
    await save(); assert.equal(config.ProcessingWidth, 4800); assert.equal(config.ProcessingHeight, 2700);
    delete config.ProcessingWidth; delete config.ProcessingHeight;
    delete config.StreamWidth; delete config.StreamHeight;
    await load();
    assert.equal(controls.ProcessingResolution.value, '3840x2160');
    assert.equal(controls.StreamResolution.value, '3840x2160');
    assert.equal(controls.ProcessingResolution.options.length, presets.length);
    delete config.CacheMode; delete config.MemoryBudgetMb;
    await load();
    assert.equal(controls.CacheMode.value, 'memory');
    assert.equal(controls.MemoryBudgetMb.value, '0');
    await save();
    assert.equal(config.CacheMode, 'memory');
    assert.equal(config.MemoryBudgetMb, 0);
    assert.equal(config.CacheDirectory, original.CacheDirectory);
    console.log('PASS: all 36 resolution pairs, device approvals, saved sizes, memory/disk controls, budget display and compatible defaults.');
    delete config.EnableFrameInterpolation;
    await load();
    assert.equal(controls.EnableFrameInterpolation.checked, true);
    await save();
    assert.equal(config.EnableFrameInterpolation, true);
    const savedSettings = structuredClone(config);
    const checkUpdate = () => controls.CheckVsrUpdate.listeners.click();
    const installUpdate = () => controls.InstallVsrUpdate.listeners.click();
    assert.equal(updateChecks, 0, 'Opening/saving settings must not check GitHub automatically');
    assert.equal(updateInstalls, 0);
    assert.equal(controls.InstallVsrUpdate.hidden, true);
    await checkUpdate();
    assert.equal(controls.VsrUpdateStatus.textContent, 'Up to date.');
    assert.equal(controls.InstallVsrUpdate.hidden, true);
    updateReply = { ...updateReply, latestVersion: '1.5.0', canInstall: true, updateAvailable: true,
        message: 'Version 1.5.0 is available.' };
    await checkUpdate();
    assert.equal(controls.InstallVsrUpdate.hidden, false);
    assert.equal(controls.InstallVsrUpdate.textContent, 'Install 1.5.0');
    assert.equal(updateInstalls, 0, 'Checking must not install');
    updateError = { responseJSON: { message: 'GitHub is temporarily unavailable.' } };
    await checkUpdate();
    assert.equal(controls.VsrUpdateStatus.textContent, updateError.responseJSON.message);
    assert.equal(controls.InstallVsrUpdate.hidden, true, 'Failed check must hide stale update availability');
    assert.equal(controls.CheckVsrUpdate.disabled, false);
    updateError = null;
    let releaseCheck;
    updateWait = new Promise(resolve => { releaseCheck = resolve; });
    const pending = checkUpdate();
    const startedChecks = updateChecks;
    assert.equal(controls.CheckVsrUpdate.disabled, true);
    assert.equal(controls.InstallVsrUpdate.disabled, true);
    await checkUpdate();
    assert.equal(updateChecks, startedChecks, 'Concurrent clicks must not repeat requests');
    releaseCheck(); await pending; updateWait = null;
    installError = new Response(JSON.stringify({ message: 'No disk space. Free space and retry.' }), { status: 503 });
    await installUpdate();
    assert.equal(controls.VsrUpdateStatus.textContent, 'No disk space. Free space and retry.');
    assert.equal(controls.InstallVsrUpdate.disabled, false);
    assert.equal(controls.InstallVsrUpdate.hidden, false, 'Failed install must remain retryable');
    installError = null;
    updateReply = { ...updateReply, canInstall: false, updateAvailable: false, restartRequired: true,
        message: 'Installed. Restart Jellyfin when ready.' };
    await installUpdate();
    assert.equal(updateInstalls, 2);
    assert.equal(controls.InstallVsrUpdate.hidden, true);
    assert.equal(controls.VsrUpdateStatus.textContent, updateReply.message);
    assert.deepEqual(config, savedSettings, 'Update controls must not modify playback/device settings');
    console.log('PASS: manual update checks, explicit installation, disabled duplicate clicks, failed-check/install recovery, restart status and unchanged settings.');
})().catch(error => { console.error(error); process.exitCode = 1; });
