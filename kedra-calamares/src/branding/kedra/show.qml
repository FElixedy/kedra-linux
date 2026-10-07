/* Kedra Linux installer slideshow -- PLACEHOLDER.
 *
 *   SPDX-FileCopyrightText: 2015 Teo Mrnjavac <teo@kde.org>
 *   SPDX-FileCopyrightText: 2018 Adriaan de Groot <groot@kde.org>
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Upstream's version of this file is a six-image carousel of CachyOS artwork.
 * The artwork could not be rebranded by configuration -- the words "Welcome to
 * CachyOS" are painted into the images -- so it was removed and this text-only
 * slideshow put in its place.
 *
 * This is intentionally NOT final visual branding. It exists so that the
 * installer says true things about Kedra while it works, using only the
 * Calamares slideshow primitives (title + bullet content). When Kedra has real
 * artwork, this file goes back to being an Image carousel and nothing else in
 * the branding component has to change.
 *
 * Text is English-first and deliberately untranslated: these strings will be
 * replaced before there is any point in translating them.
 */

import QtQuick 2.15;
import calamares.slideshow 1.0;

Presentation
{
    id: presentation

    textColor: "#E6E9EC"
    titleColor: "#5AA9E6"
    fontFamily: "Cantarell"

    Rectangle {
        anchors.fill: parent
        color: "#1D2126"
        z: -1
    }

    function nextSlide() {
        presentation.goToNextSlide();
    }

    Timer {
        id: advanceTimer
        interval: 20000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: nextSlide()
    }

    Slide {
        title: "Welcome to Kedra Linux"
        content: [
            "Kedra Linux is a rolling-release desktop system built on Arch Linux and CachyOS.",
            "GNOME is the desktop, Btrfs is the filesystem, and updates arrive continuously rather than as releases.",
            "This installer is setting all of that up now."
        ]
    }

    Slide {
        title: "Built on CachyOS"
        content: [
            "Kedra does not reinvent the parts CachyOS already does well.",
            "The kernel, the CPU-optimised package repositories, the performance defaults and the hardware detection all come from CachyOS.",
            "Kedra adds its own identity, its own package repository, and a GNOME-first desktop on top."
        ]
    }

    Slide {
        title: "Rolling, not versioned"
        content: [
            "There is no next release to wait for and no upgrade to schedule.",
            "A normal system update brings in everything: kernels, drivers, desktop and applications.",
            "Btrfs snapshots are taken so you can go back if an update does not suit you."
        ]
    }

    Slide {
        title: "Early days"
        content: [
            "This is a development build of Kedra Linux.",
            "Final artwork, wallpapers and the finished desktop experience are not part of it yet -- what you see here is placeholder.",
            "What is real is the system underneath: it installs, it boots, and it updates."
        ]
    }

    function onActivate() {
        presentation.currentSlide = 0;
    }

    function onLeave() {
    }
}
