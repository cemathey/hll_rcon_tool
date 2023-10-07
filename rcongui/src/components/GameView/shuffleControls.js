import React, {useState} from "react";
import {handle_http_errors, postData, showResponse} from "../../utils/fetchUtils";
import {Box, Button, Card, FormControl, FormControlLabel, FormLabel, Grid, Radio, RadioGroup} from "@material-ui/core";
import {ToggleButton, ToggleButtonGroup} from "@material-ui/lab";

const ShuffleControls = () => {
    /* TODO: should make an endpoint to provide these dynamically */
    const METHODS = [
        {method: "random_shuffle", label: "Random Shuffle"},
        {method: "player_level", label: "By Player Level"},
        {method: "split_shuffle", label: "Split Shuffle"},
    ];

    const [shuffleMethod, setShuffleMethod] = useState(METHODS[0].method);

    const handleChange = (event, method) => setShuffleMethod(method);
    const handleSubmit = (e) => {
        e.preventDefault();
        return postData(`${process.env.REACT_APP_API_URL}do_shuffle_teams`, {
            shuffle_method: shuffleMethod,
        })
            .then((response) =>
                showResponse(response, `Team Shuffle by ${shuffleMethod}`, true)
            )
            .catch(handle_http_errors);
    };

    return (
        <Grid item xs={12}>
            <Card>
                <Box p={2}>
                    <form>
                        <FormControl>
                            <Grid container direction={"column"} spacing={2}>
                                <Grid item>
                                    <FormLabel>Shuffle Controls</FormLabel>
                                </Grid>
                                <Grid item>
                                    <ToggleButtonGroup
                                        name="shuffle_method"
                                        value={shuffleMethod}
                                        onChange={handleChange}
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

                                <Grid item>
                                    <Button type="submit"
                                            variant="contained"
                                            color="primary"
                                            onClick={handleSubmit}>
                                        Shuffle Teams
                                    </Button>
                                </Grid>
                            </Grid>
                        </FormControl>
                    </form>
                </Box>
            </Card>
        </Grid>
    );
};

export default ShuffleControls;