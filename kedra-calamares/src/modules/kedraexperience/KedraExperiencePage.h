/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2026 Kedra Linux
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *   Calamares is Free Software: see the License-Identifier above.
 */

#ifndef KEDRAEXPERIENCEPAGE_H
#define KEDRAEXPERIENCEPAGE_H

#include <QJsonObject>
#include <QList>
#include <QString>
#include <QWidget>

class QButtonGroup;
class QCheckBox;
class QLabel;
class QRadioButton;
class QVBoxLayout;

/** @brief One user-selectable Experience module, as the resolver described it.
 *
 * Everything here comes from `kedra-experience profiles`. Nothing about which
 * modules exist, what they are called or whether they may be offered is written
 * down in this installer.
 */
struct KedraModule
{
    QString id;
    QString name;
    QString description;
    bool defaultEnabled = false;
};

/** @brief The Kedra Experience choice: a product, and for Custom, its modules.
 *
 * Deliberately plain. This is the M1.1 shape of the page -- correct behaviour,
 * no visual design -- and it is expected to be replaced when the installer gets
 * its real appearance. What must survive that replacement is the contract: the
 * page produces a selection document and knows nothing else.
 */
class KedraExperiencePage : public QWidget
{
    Q_OBJECT

public:
    explicit KedraExperiencePage( QWidget* parent = nullptr );

    /** @brief Build the choices from a `profiles` document.
     *
     * @p basePreset is the preset whose modules the Custom list is built from.
     * Returns false, with a message on the page, if the document does not
     * describe what this page needs -- in which case the page stays unusable
     * and the step cannot be left.
     */
    bool setProfiles( const QJsonObject& profiles, const QString& basePreset );

    /** @brief Show a hard failure instead of any choices. */
    void setError( const QString& text );

    /** @brief The selection document, exactly as `resolve` accepts it. */
    QJsonObject selection() const;

    /** @brief False until a usable set of choices has been loaded. */
    bool isReady() const { return m_ready; }

signals:
    void selectionChanged();

private:
    void rebuildModuleVisibility();

    QVBoxLayout* m_layout = nullptr;
    QLabel* m_status = nullptr;
    QWidget* m_productArea = nullptr;
    QWidget* m_moduleArea = nullptr;
    QButtonGroup* m_products = nullptr;
    QRadioButton* m_customButton = nullptr;

    // The preset each product radio selects. "" for Custom, which uses
    // m_basePreset plus whatever the checkboxes say.
    QList< QPair< QRadioButton*, QString > > m_productPresets;
    QList< QPair< QCheckBox*, KedraModule > > m_moduleBoxes;

    QString m_basePreset;
    bool m_ready = false;
};

#endif  // KEDRAEXPERIENCEPAGE_H
