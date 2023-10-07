import React, {useState} from "react";
import {handle_http_errors, postData, showResponse} from "../../utils/fetchUtils";
import {
    Box,
    Button,
    Card,
    Checkbox,
    FormControlLabel,
    FormGroup,
    FormLabel,
    Grid,
    Switch,
    TextField,
    Typography
} from "@material-ui/core";
import {ToggleButton, ToggleButtonGroup} from "@material-ui/lab";

const BalanceControls = () => {
    /* TODO: should make an endpoint to provide these dynamically */
    const METHODS = [
        {method: "arrival_most_recent", label: "Most Recently Joined"},
        {method: "arrival_least_recent", label: "Least Recently Joined"},
        {method: "random", label: "Random Selection"},
    ];
    const roleGroups = [
        {
            id: 0,
            label: "Commander",
            roles: new Set([0]),
        },
        {
            id: 1,
            label: "Armor",
            roles: new Set([1, 2]),
        },
        {
            id: 2,
            label: "Recon",
            roles: new Set([3, 4]),
        },
        {
            id: 3,
            label: "Infantry",
            roles: new Set([5, 6, 7, 8, 9, 10, 11, 12, 13]),
        },
    ];

    const [roles, setRoles] = useState([
        {
            id: 0,
            checked: false,
            value: "armycommander",
            label: "Army Commander",
        },
        {
            id: 1,
            checked: false,
            value: "tankcommander",
            label: "Tank Commander",
        },
        {
            id: 2,
            checked: false,
            value: "crewman",
            label: "Crewman",
        },
        {
            id: 3,
            checked: false,
            value: "spotter",
            label: "Spotter",
        },
        {
            id: 4,
            checked: false,
            value: "sniper",
            label: "Sniper",
        },
        {
            id: 5,
            checked: false,
            value: "officer",
            label: "Officer",
        },
        {
            id: 6,
            checked: false,
            value: "rifleman",
            label: "Rifleman",
        },
        {
            id: 7,
            checked: false,
            value: "assault",
            label: "Assault",
        },
        {
            id: 8,
            checked: false,
            value: "automaticrifleman",
            label: "Automatic Rifleman",
        },
        {
            id: 9,
            checked: false,
            value: "medic",
            label: "Medic",
        },
        {
            id: 10,
            checked: false,
            value: "support",
            label: "Support",
        },
        {
            id: 11,
            checked: false,
            value: "heavymachinegunner",
            label: "Machine Gunner",
        },
        {
            id: 12,
            checked: false,
            value: "antitank",
            label: "Anti-Tank",
        },
        {
            id: 13,
            checked: false,
            value: "engineer",
            label: "Engineer",
        },
    ]);

    const [balanceMethod, setBalanceMethod] = useState(METHODS[0].method);
    const [immuneLevel, setImmuneLevel] = useState(0);
    const [immuneSeconds, setImmuneSeconds] = useState(0);
    const [includeTeamless, setIncludeTeamless] = useState(false);
    const [swapOnDeath, setSwapOnDeath] = useState(false);

    const handleBalanceChange = (event, method) => setBalanceMethod(method);
    // TODO: add error handling for out of bounds conditions
    const handleImmuneLevelChange = (value) => setImmuneLevel(value);
    const handleImmuneSecondsChange = (value) => setImmuneSeconds(value);

    const determineCheckboxState = (groupId) => {
        /*
         * Return whether a parent checkbox should be checked or indeterminate
         * based on the state of its children
         */
        const group = roleGroups.find((g) => g.id === groupId);
        const selectedRoles = roles.filter((r) => group.roles.has(r.id));

        let state = {};
        if (selectedRoles.every((r) => r.checked === true)) {
            // all checked = checked
            state = {checked: true, indeterminate: false};
        } else if (selectedRoles.some((r) => r.checked === true)) {
            // some checked = indeterminate
            state = {checked: false, indeterminate: true};
        } else {
            // all unchecked = unchecked
            state = {checked: false, indeterminate: false};
        }

        return state;
    };

    const handleRoleGroupsChange = (groupId, checkboxState) => {
        /*
         * Check or uncheck each child checkbox when the parent is clicked
         */
        let newState = false;

        if (!checkboxState.checked) {
            newState = true;
        }

        const group = roleGroups.find((g) => g.id === groupId);
        const selectedRoleIds = roles
            .filter((r) => group.roles.has(r.id))
            .map((r) => r.id);

        setRoles(
            roles.map((r) => {
                if (selectedRoleIds.includes(r.id)) {
                    return {
                        ...r,
                        checked: newState,
                    };
                } else {
                    return r;
                }
            })
        );
    };

    const handleRolesChange = (id) => {
        /*
         * Toggle the state of a checkbox when clicked
         */
        setRoles(
            roles.map((r) => {
                if (r.id === id) {
                    return {
                        ...r,
                        checked: !r.checked,
                    };
                } else {
                    return r;
                }
            })
        );
    };

    const selectedRoleNames = () =>
        roles.filter((r) => r.checked).map((r) => r.value);

    const handleSubmit = (e) => {
        e.preventDefault();
        return postData(`${process.env.REACT_APP_API_URL}do_even_teams`, {
            rebalance_method: balanceMethod,
            immune_level: immuneLevel,
            immune_roles: selectedRoleNames(),
            immune_seconds: immuneSeconds,
            include_teamless: includeTeamless,
            swap_on_death: swapOnDeath,
        })
            .then((response) =>
                showResponse(response, `Team Balance by ${balanceMethod}`, true)
            )
            .catch(handle_http_errors);
    };

    return (
        <Grid item>
            <Card>
                <Box p={2}>
                    <form>
                        <Grid container direction={"column"} spacing={3}>
                            <Grid item>
                                <FormLabel>Balance Method</FormLabel>
                            </Grid>
                            <Grid item xs={12}>
                                <ToggleButtonGroup
                                    name="shuffle_method"
                                    value={balanceMethod}
                                    onChange={handleBalanceChange}
                                    exclusive
                                >
                                    {METHODS.map((m) => {
                                        return (
                                            <ToggleButton
                                                key={m.method}
                                                value={m.method}
                                                aria-label={m.lable}
                                            >{m.label}</ToggleButton>
                                        );
                                    })}
                                </ToggleButtonGroup>
                            </Grid>
                            <Grid item xs={12}>
                                <Button type="submit" variant="contained" color="primary" onClick={handleSubmit}>
                                    Balance Teams
                                </Button>
                            </Grid>
                            <Grid item xs={12}>
                                <Typography variant="subtitle1"> Additional Balance configurations</Typography>
                            </Grid>
                            <Grid item container xs={12} spacing={2} justify={"center"}>
                                <Grid item>
                                    <TextField
                                        id="outlined-number"
                                        helperText="Seconds until player is swappable again"
                                        label="Immune Level"
                                        type="number"
                                        value={immuneLevel}
                                        onChange={(e) => handleImmuneLevelChange(e.target.value)}
                                        InputLabelProps={{
                                            shrink: true,
                                        }}
                                        InputProps={{
                                            inputProps: {
                                                min: 1
                                            }
                                        }}
                                        variant="outlined"
                                    />
                                </Grid>
                                <Grid item>
                                    <TextField
                                        id="outlined-number"
                                        helperText="Players below this level will not be swapped"
                                        label="Immune Level"
                                        type="number"
                                        value={immuneSeconds}
                                        onChange={(e) => handleImmuneSecondsChange(e.target.value)}
                                        InputLabelProps={{
                                            shrink: true,
                                        }}
                                        InputProps={{
                                            inputProps: {
                                                min: 1
                                            }
                                        }}
                                        variant="outlined"
                                    />
                                </Grid>
                            </Grid>
                            <Grid item xs={12}>
                                <FormGroup row style={{justifyContent:"center"}}>
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                checked={includeTeamless}
                                                onChange={(e) => setIncludeTeamless(e.target.checked)}
                                                name="includeTeamless"
                                                color="primary"
                                            />
                                        }
                                        label="Include Teamless Players"
                                    />
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                checked={swapOnDeath}
                                                onChange={(e) => setSwapOnDeath(e.target.checked)}
                                                name="swapOnDeath"
                                                color="primary"
                                            />
                                        }
                                        label="Swap On Death"
                                    />
                                </FormGroup>
                            </Grid>
                            <Grid item xs={12}>
                                {roleGroups.map((g) => {
                                    const checkboxState = determineCheckboxState(g.id);
                                    return (
                                        <>
                                            <FormGroup>
                                                <FormControlLabel
                                                    label={g.label}
                                                    control={
                                                        <Checkbox
                                                            checked={checkboxState.checked}
                                                            indeterminate={checkboxState.indeterminate}
                                                            onChange={() =>
                                                                handleRoleGroupsChange(g.id, checkboxState)
                                                            }
                                                        />
                                                    }
                                                />
                                            </FormGroup>
                                            <FormGroup>
                                                {roles
                                                    .filter((r) => g.roles.has(r.id))
                                                    .map((r) => {
                                                        return (
                                                            <FormControlLabel
                                                                label={r.label}
                                                                control={
                                                                    <Checkbox
                                                                        checked={r.checked}
                                                                        onChange={() => handleRolesChange(r.id)}
                                                                    />
                                                                }
                                                            />
                                                        );
                                                    })}
                                            </FormGroup>
                                        </>
                                    );
                                })}
                            </Grid>

                        </Grid>
                    </form>
                </Box>
            </Card>
        </Grid>
    );
};

export default BalanceControls;