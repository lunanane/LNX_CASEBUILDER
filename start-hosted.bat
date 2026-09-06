@echo off
rem ---------------------------------------------------------------------------
rem  hwcase -- start the editor the way the server runs it.
rem
rem    start-hosted.bat            first free port from 8497, opens a browser
rem    start-hosted.bat 9000       pick the port yourself
rem    start-hosted.bat 9000 bare  no auto-reload, no browser
rem
rem  Same code, same venv, same everything as start.bat -- one variable
rem  different. HWCASE_HOSTED puts the server into the posture a public
rem  instance runs in: no scene directory, no writable part library, no output
rem  files. The project lives in the browser and leaves it as a .yaml you save.
rem
rem  This exists so "what the server does" is something you can look at rather
rem  than something you find out about after deploying. The two modes differ in
rem  ways that are invisible until you hit them -- your scenes are not there,
rem  an imported board goes into the file instead of the palette, a render is
rem  refused while another is running -- and none of that is worth meeting for
rem  the first time on the live instance.
rem
rem  It does NOT touch backend\scenes. Your own work stays where start.bat
rem  keeps it; this one cannot see it, which is the whole point.
rem
rem  Ports are deliberately different -- 8487 local, 8497 hosted -- so both can
rem  run at once and a bookmark keeps meaning what it meant.
rem
rem  To put the same thing on a server, see docs\hosting.md.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

rem setlocal keeps this out of the shell you launched from, so a later
rem start.bat in the same window is still the local editor.
set "HWCASE_HOSTED=1"

call "%~dp0start.bat" %*
exit /b %ERRORLEVEL%
