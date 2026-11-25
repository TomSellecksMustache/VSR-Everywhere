sub init()
    m.video = m.top.FindNode("video")
    m.status = m.top.FindNode("status")
    m.device = CreateObject("roDeviceInfo")
    display = m.device.GetDisplayProperties()
    m.displayHdr10 = display.Hdr10
    m.hdrMode = "unreported"
    print "HDRTEST "; FormatJson({event: "display", hdr10: display.Hdr10, hlg: display.HLG, dolbyVision: display.DolbyVision})
    config = ParseJson(ReadAsciiFile("pkg:/probe.json"))
    if config = invalid
        m.status.text = "Missing probe.json"
        return
    end if
    m.video.ObserveField("state", "onState")
    m.video.ObserveField("streamingSegment", "onSegment")
    m.video.ObserveField("downloadedSegment", "onDownloaded")
    content = CreateObject("roSGNode", "ContentNode")
    content.url = config.url
    content.streamFormat = "hls"
    content.title = "VSR Auto HDR probe"
    m.video.content = content
    m.video.SetFocus(true)
    m.video.control = "play"
end sub

sub updateStatus()
    m.status.text = "Display HDR10: " + FormatJson(m.displayHdr10) + " | Stream: " + m.hdrMode + " | " + m.video.state
end sub

sub onState()
    print "HDRTEST "; FormatJson({event: "state", state: m.video.state, errorCode: m.video.errorCode})
    if m.video.state = "error"
        print "HDRTEST "; FormatJson({event: "error", message: m.video.errorMsg, detail: m.video.errorStr})
    end if
    updateStatus()
end sub

sub onSegment(message as object)
    segment = message.GetData()
    if segment = invalid then return
    if segment.hdrModeStr <> invalid then m.hdrMode = segment.hdrModeStr
    print "HDRTEST "; FormatJson({event: "playing_segment", hdr: segment.hdrModeStr, sequence: segment.segSequence, width: segment.width, height: segment.height, videoMode: m.device.GetVideoMode()})
    updateStatus()
end sub

sub onDownloaded(message as object)
    segment = message.GetData()
    if segment = invalid then return
    print "HDRTEST "; FormatJson({event: "downloaded_segment", hdrMode: segment.HdrMode, sequence: segment.SegSequence, status: segment.Status})
end sub
