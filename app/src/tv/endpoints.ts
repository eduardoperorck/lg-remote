/**
 * URIs SSAP do webOS.
 *
 * Transcrito de `aiowebostv/endpoints.py` para não haver divergência entre o que o
 * projeto Python mandava e o que o app manda.
 */

export const GET_SERVICES = "api/getServiceList";
export const SET_MUTE = "audio/setMute";
export const GET_AUDIO_STATUS = "audio/getStatus";
export const GET_VOLUME = "audio/getVolume";
export const SET_VOLUME = "audio/setVolume";
export const VOLUME_UP = "audio/volumeUp";
export const VOLUME_DOWN = "audio/volumeDown";
export const GET_CURRENT_APP_INFO = "com.webos.applicationManager/getForegroundAppInfo";
export const LAUNCH_APP = "com.webos.applicationManager/launch";
export const GET_APPS = "com.webos.applicationManager/listLaunchPoints";
export const GET_APPS_ALL = "com.webos.applicationManager/listApps";
export const GET_APP_STATUS = "com.webos.service.appstatus/getAppStatus";
export const SEND_ENTER = "com.webos.service.ime/sendEnterKey";
export const SEND_DELETE = "com.webos.service.ime/deleteCharacters";
export const INSERT_TEXT = "com.webos.service.ime/insertText";
export const SET_3D_ON = "com.webos.service.tv.display/set3DOn";
export const SET_3D_OFF = "com.webos.service.tv.display/set3DOff";
export const GET_SOFTWARE_INFO = "com.webos.service.update/getCurrentSWInformation";
export const MEDIA_PLAY = "media.controls/play";
export const MEDIA_STOP = "media.controls/stop";
export const MEDIA_PAUSE = "media.controls/pause";
export const MEDIA_REWIND = "media.controls/rewind";
export const MEDIA_FAST_FORWARD = "media.controls/fastForward";
export const MEDIA_CLOSE = "media.viewer/close";
export const POWER_OFF = "system/turnOff";
export const POWER_ON = "system/turnOn";
export const SHOW_MESSAGE = "system.notifications/createToast";
export const CLOSE_TOAST = "system.notifications/closeToast";
export const CREATE_ALERT = "system.notifications/createAlert";
export const CLOSE_ALERT = "system.notifications/closeAlert";
export const LAUNCHER_CLOSE = "system.launcher/close";
export const GET_APP_STATE = "system.launcher/getAppState";
export const GET_SYSTEM_INFO = "system/getSystemInfo";
export const LAUNCH = "system.launcher/launch";
export const OPEN = "system.launcher/open";
export const GET_SYSTEM_SETTINGS = "settings/getSystemSettings";
export const TV_CHANNEL_DOWN = "tv/channelDown";
export const TV_CHANNEL_UP = "tv/channelUp";
export const GET_TV_CHANNELS = "tv/getChannelList";
export const GET_CHANNEL_INFO = "tv/getChannelProgramInfo";
export const GET_CURRENT_CHANNEL = "tv/getCurrentChannel";
export const GET_INPUTS = "tv/getExternalInputList";
export const SET_CHANNEL = "tv/openChannel";
export const SET_INPUT = "tv/switchInput";
export const CLOSE_WEB_APP = "webapp/closeWebApp";
export const INPUT_SOCKET = "com.webos.service.networkinput/getPointerInputSocket";
export const CALIBRATION = "externalpq/setExternalPqData";
export const GET_CALIBRATION = "externalpq/getExternalPqData";
export const GET_SOUND_OUTPUT = "com.webos.service.apiadapter/audio/getSoundOutput";
export const CHANGE_SOUND_OUTPUT = "com.webos.service.apiadapter/audio/changeSoundOutput";
export const GET_POWER_STATE = "com.webos.service.tvpower/power/getPowerState";
export const TURN_OFF_SCREEN = "com.webos.service.tvpower/power/turnOffScreen";
export const TURN_ON_SCREEN = "com.webos.service.tvpower/power/turnOnScreen";
export const GET_CONFIGS = "config/getConfigs";
export const GET_MEDIA_FOREGROUND_APP_INFO = "com.webos.media/getForegroundAppInfo";
export const GET_CONNECTION_INFO = "com.webos.service.connectionmanager/getinfo";

// webOS TV internal Luna API endpoints
export const LUNA_SET_CONFIGS = "com.webos.service.config/setConfigs";
export const LUNA_SET_SYSTEM_SETTINGS = "com.webos.settingsservice/setSystemSettings";
export const LUNA_TURN_ON_SCREEN_SAVER = "com.webos.service.tvpower/power/turnOnScreenSaver";
export const LUNA_SHOW_INPUT_PICKER = "com.webos.surfacemanager/showInputPicker";
