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
for (const match of html.matchAll(/<(input|select|div|form|button|p)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
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

let config = {
    Enabled: true, EnableAllDevices: false, Backend: 'nvidia', EnabledDeviceIds: ['device-1'],
    ProcessingWidth: 5120, ProcessingHeight: 2880, StreamWidth: 3840, StreamHeight: 2160,
    PythonPath: 'python', FfmpegPath: 'ffmpeg', FfprobePath: 'ffprobe', CacheDirectory: 'cache',
    CacheMode: 'disk', MemoryBudgetMb: 0, EffectiveMemoryBudgetMb: 7168,
    CacheMaxGb: 10, CacheMinFreeGb: 5, CacheIdleMinutes: 10, EnableRollingCache: false,
    RollingRewindMinutes: 10, VideoMbps: 40, VsrQuality: 'HIGH', VsrStrength: 0.75,
    NvencPreset: 'p5', NvencTune: 'hq', NvencLookahead: 12, NvencSpatialAq: true,
    NvencTemporalAq: false, NvencCq: 0, SegmentType: 'mpegts', MaxSessions: 1,
    SessionMinutes: 180, FutureSetting: 'keep me',
    EnableNeuralRendering: false, ContentProfile: 'auto', EnableAutoHdr: false, EnableFrameInterpolation: true
};
const pluginId = '7ca129d0-7454-41c7-aad6-55250b1a7b5b';
vm.runInNewContext(script, {
    document: { querySelector: () => controls.VsrEverywherePage, createElement: tag => new Element(tag) },
    ApiClient: {
        getPluginConfiguration: async id => { assert.equal(id, pluginId); return structuredClone(config); },
        updatePluginConfiguration: async (id, cfg) => { assert.equal(id, pluginId); config = structuredClone(cfg); return {}; },
        getUrl: name => name,
        getJSON: async () => ({ Items: [{ Id: 'device-1', Name: 'Living Room', AppName: 'Roku' }] })
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
        controls.EnableFrameInterpolation.checked = enabled;
        await save(); await load();
        assert.equal(config.EnableFrameInterpolation, enabled);
        assert.equal(controls.EnableFrameInterpolation.checked, enabled);
    }
    controls.ContentProfile.value = 'animation';
    controls.EnableNeuralRendering.checked = true;
    controls.EnableAutoHdr.checked = true;
    await save(); await load();
    assert.equal(config.ContentProfile, 'animation');
    assert.equal(config.EnableNeuralRendering, true);
    assert.equal(config.EnableAutoHdr, true);
    assert.equal(controls.ContentProfile.value, 'animation');
    assert.equal(controls.EnableNeuralRendering.checked, true);
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
    delete config.ContentProfile; delete config.EnableFrameInterpolation;
    await load();
    assert.equal(controls.ContentProfile.value, 'auto');
    assert.equal(controls.EnableFrameInterpolation.checked, true);
    await save();
    assert.equal(config.ContentProfile, 'auto');
    assert.equal(config.EnableFrameInterpolation, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
